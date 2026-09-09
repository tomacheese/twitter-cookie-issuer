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


class _DynamicUrlPage:
    """`_has_login_form` の待機中に SPA が /home へ非同期遷移する挙動を模した page。

    `locator().first.wait_for()` の呼び出し (= _has_login_form が行う待機) を
    トリガーとして url を書き換えることで、「待機中に状態が変わる」レースを再現する。
    """

    def __init__(self, initial_url: str, url_after_login_form_wait: str):
        self._url = initial_url
        self._url_after_login_form_wait = url_after_login_form_wait
        self.goto = MagicMock()

    @property
    def url(self):
        return self._url

    def locator(self, _selector):
        mock_locator = MagicMock()

        def wait_for(*_args, **_kwargs):
            self._url = self._url_after_login_form_wait
            raise PlaywrightTimeoutError("login form not found")

        mock_locator.first.wait_for.side_effect = wait_for
        return mock_locator


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

    @patch("src.login._launch_context")
    @patch("src.login.sync_playwright")
    def test_home_reached_during_login_form_wait_returns_valid(
        self, mock_sync_playwright, mock_launch_context
    ):
        """_has_login_form の待機中に SPA が /home へ遷移していれば VALID とすべき。"""
        page = _DynamicUrlPage(
            initial_url="https://x.com/",
            url_after_login_form_wait="https://x.com/home",
        )

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
    def test_login_route_reached_during_login_form_wait_returns_invalid(
        self, mock_sync_playwright, mock_launch_context
    ):
        """_has_login_form の待機中に SPA が明示的なログイン画面へ遷移していれば INVALID とすべき。"""
        page = _DynamicUrlPage(
            initial_url="https://x.com/",
            url_after_login_form_wait="https://x.com/i/flow/login",
        )

        playwright_cm, browser, context = _make_playwright_mocks(page)
        mock_sync_playwright.return_value = playwright_cm
        mock_launch_context.return_value = (browser, context)

        result, refreshed = verify_and_refresh_cookie(self.cookies, proxy=None)

        self.assertEqual(result, VerificationResult.INVALID)
        self.assertIsNone(refreshed)

    @patch("src.login._launch_context")
    @patch("src.login.sync_playwright")
    def test_unexpected_state_warning_includes_sanitized_url_and_elapsed_ms(
        self, mock_sync_playwright, mock_launch_context
    ):
        """判定不能時の Sentry 警告に sanitize 済み final_url と elapsed_ms を含み、query/fragment の秘匿情報は含まないこと。"""
        page = MagicMock()
        page.url = (
            "https://x.com/some/unexpected/route"
            "?secret_token=super-secret&session=abc#fragment-secret"
        )
        page.locator.return_value.first.wait_for.side_effect = PlaywrightTimeoutError(
            "login form not found"
        )

        playwright_cm, browser, context = _make_playwright_mocks(page)
        mock_sync_playwright.return_value = playwright_cm
        mock_launch_context.return_value = (browser, context)

        with patch("src.login.sentry_sdk.capture_message") as mock_capture:
            result, refreshed = verify_and_refresh_cookie(self.cookies, proxy=None)

        self.assertEqual(result, VerificationResult.INDETERMINATE)
        self.assertIsNone(refreshed)

        mock_capture.assert_called_once()
        message = mock_capture.call_args.args[0]
        self.assertIn("phase=state_check", message)
        self.assertIn("elapsed_ms=", message)
        self.assertIn("final_url=https://x.com/some/unexpected/route", message)
        self.assertNotIn("secret_token", message)
        self.assertNotIn("super-secret", message)
        self.assertNotIn("fragment-secret", message)


if __name__ == "__main__":
    unittest.main()
