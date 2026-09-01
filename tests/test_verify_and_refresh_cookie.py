"""verify_and_refresh_cookie() の navigation timeout 分類の回帰テスト。

goto が timeout で失敗しても、page.url が既に成功条件 (x.com/home) を
満たしていれば VALID として扱うべきという契約を検証する。
"""
import unittest
from unittest.mock import MagicMock, patch

from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

from src.login import VerificationResult, verify_and_refresh_cookie


def _make_playwright_mocks(page: MagicMock):
    """sync_playwright()/_launch_context() をこの page を返すようにモックする。"""
    playwright_cm = MagicMock()
    playwright_cm.__enter__.return_value = MagicMock()
    playwright_cm.__exit__.return_value = False

    browser = MagicMock()
    context = MagicMock()
    context.new_page.return_value = page
    return playwright_cm, browser, context


class TestVerifyAndRefreshCookieNavigationTimeout(unittest.TestCase):
    def setUp(self):
        self.cookies = {"ct0": "old-ct0", "auth_token": "old-auth-token"}

    @patch("src.login._launch_context")
    @patch("src.login.sync_playwright")
    def test_timeout_but_final_url_is_home_returns_valid(
        self, mock_sync_playwright, mock_launch_context
    ):
        """2 回とも goto が timeout しても、最終的に page.url が /home なら VALID。"""
        page = MagicMock()
        page.url = "https://x.com/home"
        page.goto.side_effect = [
            PlaywrightTimeoutError("timeout 1"),
            PlaywrightTimeoutError("timeout 2"),
        ]

        playwright_cm, browser, context = _make_playwright_mocks(page)
        mock_sync_playwright.return_value = playwright_cm
        mock_launch_context.return_value = (browser, context)
        context.cookies.return_value = [
            {"name": "ct0", "value": "fresh-ct0"},
            {"name": "auth_token", "value": "fresh-auth-token"},
        ]

        result, refreshed = verify_and_refresh_cookie(self.cookies, proxy=None)

        self.assertEqual(result, VerificationResult.VALID)
        self.assertEqual(
            refreshed, {"ct0": "fresh-ct0", "auth_token": "fresh-auth-token"}
        )

    @patch("src.login._launch_context")
    @patch("src.login.sync_playwright")
    def test_timeout_and_final_url_is_login_stays_indeterminate(
        self, mock_sync_playwright, mock_launch_context
    ):
        """goto が timeout し、page.url も success 条件を満たさないなら従来通り INDETERMINATE。"""
        page = MagicMock()
        page.url = "https://x.com/i/flow/login"
        page.goto.side_effect = [
            PlaywrightTimeoutError("timeout 1"),
            PlaywrightTimeoutError("timeout 2"),
        ]

        playwright_cm, browser, context = _make_playwright_mocks(page)
        mock_sync_playwright.return_value = playwright_cm
        mock_launch_context.return_value = (browser, context)

        result, refreshed = verify_and_refresh_cookie(self.cookies, proxy=None)

        self.assertEqual(result, VerificationResult.INDETERMINATE)
        self.assertIsNone(refreshed)


if __name__ == "__main__":
    unittest.main()
