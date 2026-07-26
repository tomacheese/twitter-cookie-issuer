"""Twitter/X 認証情報から ct0/auth_token を取得するCLIエントリーポイント。

MODE 環境変数で実行モードを切り替える。
- 未設定 or "once": 環境変数の単一アカウントで1回ログインし、
  /data/cookies.json に保存する。
- "daemon": HTTPサーバーとして常駐し、リクエストごとにログインする。
"""
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import sentry_sdk

from .config import COOKIES_PATH, SCREENSHOTS_DIR, ConfigError, load_once_config
from .error_reporting import init_sentry
from .login import LoginError, get_cookies

# daemon モードでの cookie 保存先 (ユーザー名ごとに別ファイル)。
COOKIES_DIR = Path("/data/cookies")

# X のユーザー名の許可文字集合 (英数字・アンダースコア、1〜15文字)。
# username はキャッシュファイル名の構築にそのまま使われるため、ここで検証しないとパストラバーサル (例: "../../etc/passwd") を許してしまう。
_USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,15}$")

# 同時に1件のログインしか実行しないための排他ロック。
_login_lock = threading.Lock()


def run_once() -> None:
    """once モード: 単一アカウントで1回だけログインを試行する。"""
    try:
        once_config = load_once_config()
    except ConfigError as error:
        print(f"設定エラー: {error}", file=sys.stderr)
        sys.exit(1)

    print(f"ログイン試行中... (username: {once_config.username})")
    try:
        get_cookies(
            username=once_config.username,
            password=once_config.password,
            email=once_config.email,
            otp_secret=once_config.otp_secret,
            cache_path=COOKIES_PATH,
            screenshot_dir=SCREENSHOTS_DIR,
        )
    except LoginError as error:
        sentry_sdk.capture_exception(error)
        print(f"ログインに失敗しました: {error}", file=sys.stderr)
        if error.screenshot_path:
            print(f"スクリーンショット: {error.screenshot_path}", file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        sentry_sdk.capture_exception(error)
        print(f"予期しないエラーが発生しました: {error}", file=sys.stderr)
        sys.exit(1)

    print(f"ct0 と auth_token を {COOKIES_PATH} に保存しました。")


class LoginRequestHandler(BaseHTTPRequestHandler):
    """daemon モードの HTTP リクエストハンドラ。"""

    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"status": "error", "message": "not found"})

    def do_POST(self) -> None:
        if self.path != "/login":
            self._send_json(404, {"status": "error", "message": "not found"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send_json(400, {"status": "error", "message": "invalid JSON body"})
            return

        username = body.get("username")
        password = body.get("password")
        email = body.get("email")
        otp_secret = body.get("otp_secret")

        if not username or not password:
            self._send_json(
                400, {"status": "error", "message": "username/password は必須です"}
            )
            return

        if not _USERNAME_PATTERN.match(username):
            self._send_json(
                400, {"status": "error", "message": "username の形式が不正です"}
            )
            return

        if not _login_lock.acquire(blocking=False):
            self._send_json(
                409, {"status": "conflict", "message": "別のログイン処理を実行中です"}
            )
            return

        try:
            cache_path = COOKIES_DIR / f"{username}.json"
            result = get_cookies(
                username=username,
                password=password,
                email=email,
                otp_secret=otp_secret,
                cache_path=cache_path,
                screenshot_dir=SCREENSHOTS_DIR,
                screenshot_username=username,
            )
            self._send_json(200, {"status": "ok", **result})
        except LoginError as error:
            sentry_sdk.capture_exception(error)
            payload = {"status": "error", "message": str(error)}
            if error.screenshot_path:
                payload["screenshot"] = str(error.screenshot_path)
            self._send_json(500, payload)
        except Exception as error:
            sentry_sdk.capture_exception(error)
            self._send_json(
                500, {"status": "error", "message": "internal server error"}
            )
        finally:
            _login_lock.release()

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # BaseHTTPRequestHandler のデフォルトはアクセスログを stderr に出すため、そのまま stderr に出力する (握りつぶさない)。
        sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))


def run_daemon() -> None:
    """daemon モード: HTTPサーバーとして常駐し、リクエストごとにログインする。"""
    port = int(os.environ.get("PORT", "8080"))
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), LoginRequestHandler)
    print(f"daemon モードで起動しました (port: {port})")
    server.serve_forever()


def main() -> None:
    """MODE 環境変数に応じて once/daemon を切り替えるエントリーポイント。"""
    init_sentry()
    mode = os.environ.get("MODE", "once")
    print(f"起動しました (MODE: {mode})")
    if mode == "once":
        run_once()
    elif mode == "daemon":
        run_daemon()
    else:
        print(f"不明な MODE です: {mode}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
