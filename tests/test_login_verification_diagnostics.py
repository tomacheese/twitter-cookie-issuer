"""verify_and_refresh_cookie() の診断用ヘルパー (_sanitize_url / _goto_with_retry) の回帰テスト。"""
import unittest
from unittest.mock import MagicMock

from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

from src.login import _goto_with_retry, _sanitize_url


class TestSanitizeUrl(unittest.TestCase):
    def test_strips_query_and_fragment(self):
        result = _sanitize_url("https://x.com/i/flow/login?redirect_after_login=%2Fhome#top")
        self.assertEqual(result, "https://x.com/i/flow/login")

    def test_no_query_or_fragment_is_unchanged(self):
        result = _sanitize_url("https://x.com/home")
        self.assertEqual(result, "https://x.com/home")

    def test_preserves_scheme_netloc_and_path_only(self):
        result = _sanitize_url("https://x.com:443/login?ct0=leaked-value")
        self.assertEqual(result, "https://x.com:443/login")
        self.assertNotIn("leaked-value", result)


class TestGotoWithRetry(unittest.TestCase):
    def test_succeeds_on_first_attempt_without_retry(self):
        page = MagicMock()
        _goto_with_retry(page, "https://x.com/home", 30000)
        page.goto.assert_called_once_with(
            "https://x.com/home", wait_until="load", timeout=30000
        )

    def test_retries_once_after_single_timeout_then_succeeds(self):
        page = MagicMock()
        page.goto.side_effect = [PlaywrightTimeoutError("timeout"), None]
        _goto_with_retry(page, "https://x.com/home", 30000)
        self.assertEqual(page.goto.call_count, 2)

    def test_raises_after_two_consecutive_timeouts(self):
        page = MagicMock()
        page.goto.side_effect = [
            PlaywrightTimeoutError("timeout 1"),
            PlaywrightTimeoutError("timeout 2"),
        ]
        with self.assertRaises(PlaywrightTimeoutError):
            _goto_with_retry(page, "https://x.com/home", 30000)
        self.assertEqual(page.goto.call_count, 2)

    def test_non_timeout_exception_is_not_retried(self):
        page = MagicMock()
        page.goto.side_effect = RuntimeError("network error")
        with self.assertRaises(RuntimeError):
            _goto_with_retry(page, "https://x.com/home", 30000)
        page.goto.assert_called_once()


if __name__ == "__main__":
    unittest.main()
