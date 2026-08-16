"""/login のロック排他制御 (bounded wait) と、response 書き込み時の
peer disconnect (BrokenPipeError 等) 処理の回帰テスト。
"""
import contextlib
import inspect
import io
import json
import os
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

from src import __main__ as main_module
from src.login import get_cookies


class TestLoadLoginLockTimeoutSeconds(unittest.TestCase):
    def test_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOGIN_LOCK_TIMEOUT_SECONDS", None)
            self.assertEqual(main_module._load_login_lock_timeout_seconds(), 180.0)

    def test_default_when_not_numeric(self):
        self.assertEqual(
            main_module._load_login_lock_timeout_seconds_from("not-a-number"), 180.0
        )

    def test_default_when_zero_or_negative(self):
        self.assertEqual(main_module._load_login_lock_timeout_seconds_from("0"), 180.0)
        self.assertEqual(main_module._load_login_lock_timeout_seconds_from("-1"), 180.0)

    def test_default_when_not_finite(self):
        self.assertEqual(main_module._load_login_lock_timeout_seconds_from("inf"), 180.0)
        self.assertEqual(main_module._load_login_lock_timeout_seconds_from("nan"), 180.0)

    def test_valid_value_is_used(self):
        self.assertEqual(main_module._load_login_lock_timeout_seconds_from("30"), 30.0)


class TestAcquireLoginLock(unittest.TestCase):
    def setUp(self):
        # 他のテストが保持したままにした状態を引き継がないよう新しい Lock に差し替える。
        main_module._login_lock = threading.Lock()

    def test_serializes_two_callers(self):
        # second の acquire が hold_duration 未満で成功したら、
        # 直列化されていない (並行実行された) ことになる。
        hold_duration = 0.3
        acquired_event = threading.Event()
        first_acquired = []

        def hold_lock():
            with main_module._acquire_login_lock(5.0) as acquired:
                first_acquired.append(acquired)
                acquired_event.set()
                time.sleep(hold_duration)

        first = threading.Thread(target=hold_lock)
        first.start()
        self.assertTrue(acquired_event.wait(timeout=2.0))
        self.assertEqual(first_acquired, [True])

        start = time.monotonic()
        with main_module._acquire_login_lock(5.0) as acquired:
            elapsed = time.monotonic() - start
            self.assertTrue(acquired)

        first.join(timeout=2.0)
        self.assertFalse(first.is_alive())
        # second は first の解放 (hold_duration 経過後) まで待たされたはず
        # = 直列実行されている。前段のスレッド起動オーバーヘッド分の余裕を持たせて判定する。
        self.assertGreaterEqual(elapsed, hold_duration * 0.8)

    def test_times_out_instead_of_failing_immediately(self):
        release_event = threading.Event()

        def hold_lock():
            with main_module._acquire_login_lock(5.0):
                release_event.wait(timeout=2.0)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        time.sleep(0.1)

        start = time.monotonic()
        with main_module._acquire_login_lock(0.3) as acquired:
            elapsed = time.monotonic() - start
            self.assertFalse(acquired)

        self.assertGreaterEqual(elapsed, 0.3)

        release_event.set()
        holder.join(timeout=2.0)
        self.assertFalse(holder.is_alive())

    def test_releases_lock_after_exception(self):
        with self.assertRaises(ValueError):
            with main_module._acquire_login_lock(5.0) as acquired:
                self.assertTrue(acquired)
                raise ValueError("boom")

        with main_module._acquire_login_lock(1.0) as acquired:
            self.assertTrue(acquired)


class TestGetCookiesTimingBackwardCompat(unittest.TestCase):
    def test_timing_parameter_is_optional(self):
        sig = inspect.signature(get_cookies)
        self.assertIn("timing", sig.parameters)
        self.assertIsNone(sig.parameters["timing"].default)


class TestWriteDiagnosticLog(unittest.TestCase):
    def test_writes_single_json_line_to_stderr(self):
        fields = {
            "request_id": "req-1",
            "caller": "test-client",
            "username": "example_user",
            "lock_wait_ms": 1.5,
            "verify_ms": None,
            "login_ms": 12.3,
            "total_ms": 13.8,
            "status": 200,
            "result": "ok",
        }
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            main_module._write_diagnostic_log(fields)

        lines = buf.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        parsed = json.loads(lines[0])
        self.assertEqual(parsed, fields)
        for secret_key in ("password", "otp_secret", "ct0", "auth_token"):
            self.assertNotIn(secret_key, parsed)


class TestSendJsonPeerDisconnect(unittest.TestCase):
    def _make_handler(self, write_side_effect=None):
        handler = object.__new__(main_module.LoginRequestHandler)
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()
        if write_side_effect is not None:
            handler.wfile.write.side_effect = write_side_effect
        return handler

    def test_returns_true_on_successful_write(self):
        handler = self._make_handler()
        result = handler._send_json(200, {"status": "ok"})
        self.assertTrue(result)
        handler.wfile.write.assert_called_once()

    def test_broken_pipe_returns_false_without_raising(self):
        handler = self._make_handler(write_side_effect=BrokenPipeError())
        result = handler._send_json(200, {"status": "ok"})
        self.assertFalse(result)

    def test_connection_reset_returns_false_without_raising(self):
        handler = self._make_handler(write_side_effect=ConnectionResetError())
        result = handler._send_json(200, {"status": "ok"})
        self.assertFalse(result)

    def test_connection_aborted_returns_false_without_raising(self):
        handler = self._make_handler(write_side_effect=ConnectionAbortedError())
        result = handler._send_json(200, {"status": "ok"})
        self.assertFalse(result)


class TestDoPostClientDisconnected(unittest.TestCase):
    def setUp(self):
        main_module._login_lock = threading.Lock()

    def _do_post_with_write_failure(self, write_side_effect):
        handler = object.__new__(main_module.LoginRequestHandler)
        handler.path = "/login"
        body = json.dumps(
            {"username": "example_user", "password": "pw"}
        ).encode("utf-8")
        handler.headers = {"Content-Length": str(len(body))}
        handler.rfile = io.BytesIO(body)
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()
        handler.wfile.write.side_effect = write_side_effect

        logged = {}

        def fake_write_log(fields):
            logged.update(fields)

        with (
            patch.object(main_module, "_write_diagnostic_log", side_effect=fake_write_log),
            patch.object(
                main_module, "get_cookies", return_value={"ct0": "x", "auth_token": "y"}
            ),
            patch("src.__main__.sentry_sdk.capture_exception") as mock_capture,
        ):
            handler.do_POST()

        return logged, mock_capture

    def test_result_kept_and_client_disconnected_flagged_on_peer_disconnect_during_200_response(self):
        logged, mock_capture = self._do_post_with_write_failure(BrokenPipeError())

        self.assertEqual(logged["status"], 200)
        self.assertEqual(logged["result"], "ok")
        self.assertTrue(logged["client_disconnected"])
        mock_capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
