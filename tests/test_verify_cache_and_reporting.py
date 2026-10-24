"""判定不能通知の grouping、goto の再試行、検証結果キャッシュの回帰テスト。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from patchright.sync_api import Error as PlaywrightError

import src.login as login_module
from src.login import (
    IndeterminateVerificationError,
    VerificationResult,
    _goto_with_retry,
    _report_indeterminate,
    get_cookies,
)


class TestReportIndeterminate(unittest.TestCase):
    def _report(self, exc, **kwargs):
        scope = MagicMock()
        with patch("src.login.sentry_sdk") as sdk:
            sdk.new_scope.return_value.__enter__.return_value = scope
            _report_indeterminate("navigation", 12, "https://x.com/", exc, **kwargs)
        return sdk, scope

    def test_message_is_constant_and_fingerprint_uses_net_err(self):
        sdk, scope = self._report(PlaywrightError("Page.goto: net::ERR_TUNNEL_CONNECTION_FAILED at ..."))
        sdk.capture_message.assert_called_once_with(
            "cookie verification indeterminate (navigation)", level="warning"
        )
        self.assertEqual(
            scope.fingerprint,
            ["cookie-verify-indeterminate", "navigation", "net::ERR_TUNNEL_CONNECTION_FAILED"],
        )
        scope.set_tag.assert_called_once_with("verify.phase", "navigation")

    def test_fingerprint_falls_back_to_class_name(self):
        _, scope = self._report(RuntimeError("boom"))
        self.assertEqual(
            scope.fingerprint, ["cookie-verify-indeterminate", "navigation", "RuntimeError"]
        )

    def test_error_message_omitted_when_include_error_false(self):
        _, scope = self._report(RuntimeError("secret-cookie"), include_error=False)
        context = scope.set_context.call_args.args[1]
        self.assertIsNone(context["error"])
        self.assertEqual(context["error_class"], "RuntimeError")


class TestGotoRetryOnPlaywrightError(unittest.TestCase):
    @patch("src.login.time.sleep")
    def test_retries_once_on_non_timeout_playwright_error(self, sleep):
        page = MagicMock()
        page.goto.side_effect = [PlaywrightError("net::ERR_FAILED"), None]
        _goto_with_retry(page, "https://x.com/home", 30000)
        self.assertEqual(page.goto.call_count, 2)
        sleep.assert_called_once_with(1)

    @patch("src.login.time.sleep")
    def test_raises_after_two_playwright_errors(self, _sleep):
        page = MagicMock()
        page.goto.side_effect = [PlaywrightError("a"), PlaywrightError("b")]
        with self.assertRaises(PlaywrightError):
            _goto_with_retry(page, "https://x.com/home", 30000)
        self.assertEqual(page.goto.call_count, 2)


class TestVerifyCache(unittest.TestCase):
    def setUp(self):
        login_module._verify_cache.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cache_path = Path(tmp.name) / "u.json"
        self.cookies = {"ct0": "c", "auth_token": "a"}
        self.cache_path.write_text(json.dumps(self.cookies), encoding="utf-8")

    def _get(self):
        return get_cookies("u", "p", None, None, self.cache_path, Path("shots"))

    @patch("src.login.verify_and_refresh_cookie")
    def test_second_call_hits_cache_with_same_shape(self, verify):
        verify.return_value = (VerificationResult.VALID, self.cookies)
        first = self._get()
        second = self._get()
        self.assertEqual(first, second)
        self.assertEqual(second, self.cookies)
        verify.assert_called_once()

    @patch("src.login.verify_and_refresh_cookie")
    def test_expired_entry_reverifies(self, verify):
        verify.return_value = (VerificationResult.VALID, self.cookies)
        with patch("src.login.time.monotonic", return_value=1000.0):
            self._get()
        with patch("src.login.time.monotonic", return_value=1000.0 + 601):
            self._get()
        self.assertEqual(verify.call_count, 2)

    @patch("src.login.verify_and_refresh_cookie")
    def test_ttl_zero_disables_cache(self, verify):
        verify.return_value = (VerificationResult.VALID, self.cookies)
        with patch.dict("os.environ", {"VERIFY_CACHE_TTL_SECONDS": "0"}):
            self._get()
            self._get()
        self.assertEqual(verify.call_count, 2)

    @patch("src.login.verify_and_refresh_cookie")
    def test_changed_cookie_file_misses_cache(self, verify):
        verify.return_value = (VerificationResult.VALID, self.cookies)
        self._get()
        self.cache_path.write_text(
            json.dumps({"ct0": "new", "auth_token": "a"}), encoding="utf-8"
        )
        self._get()
        self.assertEqual(verify.call_count, 2)

    @patch("src.login.verify_and_refresh_cookie")
    def test_indeterminate_invalidates_entry(self, verify):
        verify.side_effect = [
            (VerificationResult.VALID, self.cookies),
            (VerificationResult.INDETERMINATE, None),
        ]
        self._get()
        with patch("src.login.time.monotonic", return_value=1e9), \
                self.assertRaises(IndeterminateVerificationError):
            self._get()
        self.assertNotIn("u", login_module._verify_cache)


if __name__ == "__main__":
    unittest.main()
