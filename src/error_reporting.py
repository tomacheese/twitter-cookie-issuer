"""Sentry SDK (GlitchTip 互換) によるエラー送信の初期化とスクラビング処理。"""
import os

import sentry_sdk

# before_send でマスク対象とするローカル変数名 (大文字小文字を区別しない)。
_SENSITIVE_KEYS = {"password", "otp_secret"}


def init_sentry() -> None:
    """SENTRY_DSN が設定されていれば sentry_sdk を初期化する。

    未設定 (空文字含む) の場合は何もせず、Sentry 無効のまま起動を継続する。
    """
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn:
        return
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT") or "production",
        release=os.environ.get("APPLICATION_VERSION") or None,
        before_send=_scrub_before_send,
    )


def _scrub_before_send(event: dict, hint: dict) -> dict:
    """スタックトレースのローカル変数から認証情報 (password/otp_secret) をマスクする。

    Sentry SDK は既定でスタックトレースの各フレームにローカル変数の値を
    そのまま含めて送信するため、この対策がないと do_POST() の except ブロックで
    捕捉した例外経由で生パスワードが GlitchTip に送信されてしまう。
    """
    for exception in event.get("exception", {}).get("values", []):
        frames = exception.get("stacktrace", {}).get("frames", [])
        for frame in frames:
            if "vars" in frame:
                frame["vars"] = _scrub_value(frame["vars"])
    return event


def _scrub_value(value):
    """dict/list を再帰的に走査し、機密キーの値を "[Filtered]" に置換する。"""
    if isinstance(value, dict):
        return {
            key: "[Filtered]" if key.lower() in _SENSITIVE_KEYS else _scrub_value(val)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    return value
