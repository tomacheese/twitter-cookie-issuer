"""patchright を用いた x.com へのログインと ct0/auth_token 取得処理。

選定理由:
- 独自実装 (curl_cffi + x_client_transaction + ui_metrics 手動解読) は LoginEnterUserIdentifierSSO ステップで一貫して code 399 "Could not log you in now" を返すことを確認済み (詳細は KNOWLEDGE.md 参照)。
- 通常の Playwright は Chrome DevTools Protocol (CDP) 経由の自動操作が検知されブロックされるため、CDP 検知回避パッチが当たった Playwright 互換フォークである patchright を採用する。
"""
import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

import pyotp
from patchright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright


class LoginError(RuntimeError):
    """ログインフロー中に発生したエラーを表す例外。

    Attributes:
        screenshot_path: 失敗時に保存したスクリーンショットのパス (保存できなかった場合は None)。
        failure_type: 失敗の種別 ("generic_error" 等、_save_failure_screenshot に渡すものと同じ文字列)。
            未分類の場合は None。
    """

    def __init__(
        self,
        message: str,
        screenshot_path: Path | None = None,
        failure_type: str | None = None,
    ):
        super().__init__(message)
        self.screenshot_path = screenshot_path
        self.failure_type = failure_type


class IndeterminateVerificationError(RuntimeError):
    """cookie の有効性を確定できなかったことを表す例外。

    フルログインへのフォールバックは行わず、呼び出し側 (once/daemon) で
    再試行可能なエラーとして扱われることを想定する。
    """


class VerificationResult(Enum):
    """保存済み cookie の検証結果を表す3状態。"""

    VALID = "valid"
    INVALID = "invalid"
    INDETERMINATE = "indeterminate"


def _build_proxy_config() -> dict | None:
    """環境変数からブラウザ起動用のプロキシ設定を組み立てる。

    HTTPS_PROXY を優先し、なければ HTTP_PROXY を使う。どちらも未設定ならプロキシなし (None) を返す。

    `http://user:pass@host:port` 形式で認証情報が URL に埋め込まれている場合、Playwright の `proxy.server` はこれをサポートしない (Chromium 側に認証情報が渡らず、プロキシ接続がタイムアウトすることを実機で確認済み) ため、`username`/`password` の別フィールドに分離して渡す。
    """
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not proxy_url:
        return None
    parsed = urlsplit(proxy_url)
    if parsed.username is None:
        return {"server": proxy_url}
    server = urlunsplit((parsed.scheme, parsed.hostname + (
        f":{parsed.port}" if parsed.port else ""
    ), parsed.path, parsed.query, parsed.fragment))
    config = {"server": server, "username": unquote(parsed.username)}
    if parsed.password is not None:
        config["password"] = unquote(parsed.password)
    return config


def load_cached_cookies(cache_path: Path) -> dict | None:
    """キャッシュファイルから ct0/auth_token を読み込む。

    ファイルが存在しない、JSON として壊れている、必要なキーが欠けている場合はいずれも None を返し、呼び出し側でフルログインにフォールバックできるようにする。
    """
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or "ct0" not in data or "auth_token" not in data:
        return None
    return {"ct0": data["ct0"], "auth_token": data["auth_token"]}


def _save_failure_screenshot(
    page, screenshot_dir: Path, failure_type: str, username: str | None = None
) -> Path:
    """失敗時のスクリーンショットを保存し、そのパスを返す。

    ファイル名は once モードでは "{datetime}-{type}.png"、daemon モード (username 指定あり) では "{datetime}-{username}-{type}.png" となる。
    """
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    parts = [timestamp]
    if username:
        parts.append(username)
    parts.append(failure_type)
    path = screenshot_dir / f"{'-'.join(parts)}.png"
    page.screenshot(path=str(path))
    return path


def _launch_context(playwright, proxy: dict | None):
    """patchright でブラウザ・コンテキストを起動する共通処理。

    patchright 自体に CDP 検知回避のパッチが含まれているため playwright-stealth を重ねる必要はないことを確認済み。Xvfb 上での非ヘッドレス起動が必須 (headless だと検知されブロックされる)。

    Docker コンテナには既定では `/dev/dri` (GPU デバイス) が存在せず、`--use-gl`/`--enable-unsafe-swiftshader` を指定しないと WebGL のコンテキスト生成自体が失敗する (`getContext('webgl')` が null を返す) ことを確認済みのため、常に SwiftShader (ソフトウェアレンダラー) で起動する。GPU passthrough (`/dev/dri` を実 GPU 経由で渡す構成) も検証したが、レンダラー文字列の違い (SwiftShader か実 GPU か) はログイン成功・失敗と無相関であることが実機検証で判明したため (詳細は KNOWLEDGE.md §6.6/§6.8 参照)、ホスト依存の起動オプションを要求する GPU passthrough は採用しない。
    `--disable-dev-shm-usage` は Docker のデフォルト `/dev/shm` サイズ (64MB) が小さく、レンダラープロセスがクラッシュしうる既知の問題への対策 (Playwright/Puppeteer の Docker 運用で広く推奨されているフラグ)。
    """
    gl_args = [
        "--use-gl=swiftshader",
        "--enable-unsafe-swiftshader",
    ]
    browser = playwright.chromium.launch(
        headless=False,
        proxy=proxy,
        args=gl_args + ["--disable-dev-shm-usage"],
    )
    context = browser.new_context(locale="en-US")
    return browser, context


def _extract_generic_error(page, timeout: int = 3000) -> str | None:
    """画面上に汎用エラーバナーが表示されていれば、そのメッセージ本文を返す。

    "The password you entered is incorrect." 等、新オンボーディングフローの汎用エラーはいずれも icon-error-triangle アイコンと、その共通の親要素内にメッセージ本文の <p> が入る構造で表示される。
    """
    error_icon = page.locator('[data-icon="icon-error-triangle"]:visible').first
    try:
        error_icon.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeoutError:
        return None
    return error_icon.locator("xpath=ancestor::div[1]").inner_text().strip()


def _is_login_route(url: str) -> bool:
    """URL が明示的なログイン画面のものかどうかを判定する。"""
    return url.startswith("https://x.com/i/flow/login") or url.startswith(
        "https://x.com/login"
    )


def _has_login_form(page) -> bool:
    """login() が実際に使うユーザー名入力欄が画面上に存在するかどうかを判定する。

    フォーム検出自体が (ページが既に閉じている等で) 失敗しても、
    それは「未認証を確認できなかった」というだけなので広く例外を捕捉する。
    """
    try:
        page.locator("#jf-input-username_or_email").first.wait_for(
            state="visible", timeout=2000
        )
        return True
    except Exception:
        return False


def verify_and_refresh_cookie(
    cookies: dict, proxy: dict | None
) -> tuple[VerificationResult, dict | None]:
    """キャッシュされた cookie の有効性を確認する。

    ct0/auth_token をブラウザに注入して x.com/home にアクセスし、以下の3状態に分類する。

    - VALID: /home に到達できた。X 側で ct0 がローテーションされている可能性があるため、
      アクセス後に現在の ct0/auth_token を取得し直して返す
      (ローテーションされていなければ渡された値と同一になる)。
    - INVALID: 明示的なログイン画面 (URL またはログインフォームの存在) を確認できた。
      cookie が認証に使えないことが確定した場合のみこの値を返す。
    - INDETERMINATE: ナビゲーション自体が例外で失敗した場合、または /home にも
      明示的なログイン画面にも該当しない想定外の状態だった場合。timeout や
      network error だけでは cookie 失効とみなさないための分類。

    Returns:
        (VerificationResult, dict | None) のタプル。VALID の場合のみ2要素目に
        現在の ct0/auth_token を含む dict が入り、それ以外は None。
    """
    with sync_playwright() as p:
        browser, context = _launch_context(p, proxy)
        try:
            context.add_cookies(
                [
                    {
                        "name": "ct0",
                        "value": cookies["ct0"],
                        "domain": ".x.com",
                        "path": "/",
                    },
                    {
                        "name": "auth_token",
                        "value": cookies["auth_token"],
                        "domain": ".x.com",
                        "path": "/",
                    },
                ]
            )
            page = context.new_page()
            try:
                page.goto("https://x.com/home", wait_until="load", timeout=30000)
            except Exception:
                return VerificationResult.INDETERMINATE, None

            if page.url.startswith("https://x.com/home"):
                current_cookies = context.cookies("https://x.com")
                current_dict = {c["name"]: c["value"] for c in current_cookies}
                return VerificationResult.VALID, {
                    "ct0": current_dict.get("ct0", cookies["ct0"]),
                    "auth_token": current_dict.get("auth_token", cookies["auth_token"]),
                }

            if _is_login_route(page.url) or _has_login_form(page):
                return VerificationResult.INVALID, None

            return VerificationResult.INDETERMINATE, None
        finally:
            browser.close()


def login(
    username: str,
    password: str,
    email: str | None,
    otp_secret: str | None,
    proxy: dict | None,
    screenshot_dir: Path,
    screenshot_username: str | None = None,
) -> dict[str, str]:
    """patchright で実際の x.com ログインページを操作し、ct0/auth_token を取得する。

    Raises:
        LoginError: ログインに失敗した場合、または想定外の画面で停止した場合。
    """
    with sync_playwright() as p:
        browser, context = _launch_context(p, proxy)
        page = context.new_page()

        try:
            page.goto("https://x.com/i/flow/login", wait_until="load", timeout=60000)

            # .fill() だと React 側の入力ハンドラが反応せず、送信後にフォームが最初の画面まで丸ごとリセットされることを確認したため、実際のキー入力をシミュレートする press_sequentially を使う。
            username_field = page.locator("#jf-input-username_or_email").first
            username_field.wait_for(state="visible", timeout=30000)
            username_field.click()
            username_field.press_sequentially(username, delay=80)
            username_field.press("Enter")

            # ユーザー名送信直後、Castle.io 等の不正検知により "We've temporarily limited your login. Please try again later." のようなレート制限バナーが同一画面上に表示されることがある。
            # このとき begin_login API 自体は HTTP 200 で応答するため (実通信キャプチャで確認済み、詳細は KNOWLEDGE.md 参照)、検知しないまま後続のパスワード欄クリックに進むと、バナー表示によるレイアウト変化でクリックがインターセプトされ続け、原因不明のクリックタイムアウトとしてしか報告できていなかった。
            # 成功時 (バナー非表示時) の待ち時間を抑えるため、短い timeout で早期に検知し、実際の原因をエラーメッセージとして明示する。
            early_error_message = _extract_generic_error(page, timeout=1000)
            if early_error_message:
                screenshot_path = None
                try:
                    screenshot_path = _save_failure_screenshot(
                        page, screenshot_dir, "generic_error", screenshot_username
                    )
                except Exception:
                    pass
                browser.close()
                raise LoginError(
                    f"ログインエラー: {early_error_message} (url: {page.url})",
                    screenshot_path,
                    failure_type="generic_error",
                )

            # 追加の本人確認 (ユーザー名/電話番号) が挟まれることがある
            try:
                alt_identifier_field = page.locator(
                    'input[data-testid="ocfEnterTextTextInput"]'
                )
                alt_identifier_field.wait_for(state="visible", timeout=5000)
                if email:
                    alt_identifier_field.click()
                    alt_identifier_field.press_sequentially(email, delay=80)
                    alt_identifier_field.press("Enter")
                else:
                    screenshot_path = _save_failure_screenshot(
                        page, screenshot_dir, "email_verification_required", screenshot_username
                    )
                    browser.close()
                    raise LoginError(
                        "追加の本人確認 (email/phone) が要求されましたが email が未設定です。",
                        screenshot_path,
                        failure_type="email_verification_required",
                    )
            except PlaywrightTimeoutError:
                pass

            # 直前の画面 (ユーザー名入力欄) がフェードアウト等の遷移演出中に残っていると、次画面のパスワード欄と重なりクリックがインターセプトされてタイムアウトすることを確認したため、ユーザー名欄が画面から消えるのを (ベストエフォートで) 待つ。
            try:
                username_field.wait_for(state="hidden", timeout=5000)
            except PlaywrightTimeoutError:
                pass

            password_field = page.locator('input[name="password"]').first
            password_field.wait_for(state="visible", timeout=30000)
            password_field.click()
            password_field.press_sequentially(password, delay=80)
            password_field.press("Enter")

            try:
                page.wait_for_url("https://x.com/home", timeout=15000)
            except PlaywrightTimeoutError:
                error_message = _extract_generic_error(page)
                if error_message:
                    screenshot_path = _save_failure_screenshot(
                        page, screenshot_dir, "generic_error", screenshot_username
                    )
                    browser.close()
                    raise LoginError(
                        f"ログインエラー: {error_message} (url: {page.url})",
                        screenshot_path,
                        failure_type="generic_error",
                    )

                verification_field = page.locator("input.jf-code-input-field").first
                try:
                    verification_field.wait_for(state="visible", timeout=15000)
                except PlaywrightTimeoutError:
                    screenshot_path = _save_failure_screenshot(
                        page, screenshot_dir, "unexpected_state", screenshot_username
                    )
                    browser.close()
                    raise LoginError(
                        f"ホーム画面にも確認コード入力画面にも到達しませんでした (url: {page.url})",
                        screenshot_path,
                        failure_type="unexpected_state",
                    )
                if not otp_secret:
                    screenshot_path = _save_failure_screenshot(
                        page, screenshot_dir, "otp_secret_missing", screenshot_username
                    )
                    browser.close()
                    raise LoginError(
                        "2 要素認証コードの入力が要求されましたが otp_secret が未設定です。",
                        screenshot_path,
                        failure_type="otp_secret_missing",
                    )
                code = pyotp.TOTP(otp_secret).now()
                verification_field.click()
                verification_field.press_sequentially(code, delay=100)
                verification_field.press("Enter")
                try:
                    page.wait_for_url("https://x.com/home", timeout=60000)
                except PlaywrightTimeoutError:
                    error_message = _extract_generic_error(page)
                    failure_type = "generic_error" if error_message else "unexpected_state"
                    message = (
                        f"ログインエラー: {error_message} (url: {page.url})"
                        if error_message
                        else f"確認コード送信後にホーム画面へ到達しませんでした (url: {page.url})"
                    )
                    screenshot_path = _save_failure_screenshot(
                        page, screenshot_dir, failure_type, screenshot_username
                    )
                    browser.close()
                    raise LoginError(message, screenshot_path, failure_type=failure_type)

            cookies = context.cookies("https://x.com")
            cookie_dict = {c["name"]: c["value"] for c in cookies}

            ct0 = cookie_dict.get("ct0")
            auth_token = cookie_dict.get("auth_token")
            if not ct0 or not auth_token:
                screenshot_path = _save_failure_screenshot(
                    page, screenshot_dir, "cookie_extraction_failed", screenshot_username
                )
                browser.close()
                raise LoginError(
                    f"ct0 / auth_token が取得できませんでした。"
                    f"取得済み cookie: {list(cookie_dict.keys())}",
                    screenshot_path,
                    failure_type="cookie_extraction_failed",
                )

            browser.close()
        except LoginError:
            raise
        except Exception as exc:
            # 想定済みの分岐 (LoginError) 以外の例外は、要素の重なりによるクリック失敗のタイムアウト等、想定していなかった画面状態で発生しうる。
            # 生の例外のまま落として診断情報 (スクリーンショット) を失わないよう、ここで捕捉してスクリーンショットを残したうえで LoginError に変換する。
            screenshot_path = None
            try:
                screenshot_path = _save_failure_screenshot(
                    page, screenshot_dir, "unexpected_exception", screenshot_username
                )
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass
            raise LoginError(
                f"予期しないエラーが発生しました: {exc}",
                screenshot_path,
                failure_type="unexpected_exception",
            ) from exc

    return {"ct0": ct0, "auth_token": auth_token}


def _write_cache(cache_path: Path, cookies: dict) -> None:
    """ct0/auth_token をキャッシュファイルに書き込む。

    ファイル書き込みを一箇所に集約するための単一の経路。
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(cookies, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_cookies(
    username: str,
    password: str,
    email: str | None,
    otp_secret: str | None,
    cache_path: Path,
    screenshot_dir: Path,
    screenshot_username: str | None = None,
) -> dict[str, str]:
    """キャッシュされた cookie が有効ならそれを返し、無効ならフルログインする。

    キャッシュが有効だった場合、認証情報を使った再ログインは行わない。ただし X 側で ct0 がローテーションされていた場合は、キャッシュファイルを最新値に同期する。
    """
    proxy = _build_proxy_config()
    cached = load_cached_cookies(cache_path)
    if cached:
        latest = verify_and_refresh_cookie(cached, proxy)
        if latest is not None:
            if latest != cached:
                _write_cache(cache_path, latest)
            return latest

    result = login(
        username,
        password,
        email,
        otp_secret,
        proxy,
        screenshot_dir,
        screenshot_username,
    )
    _write_cache(cache_path, result)
    return result
