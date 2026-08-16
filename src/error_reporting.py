"""Sentry SDK (GlitchTip 互換) によるエラー送信の初期化とスクラビング処理。"""
import os
import re

import sentry_sdk

# before_send でマスク対象とするローカル変数名/dict キー名 (大文字小文字を区別しない)。
# password/otp_secret に加え、本ツールの出力そのものである ct0/auth_token
# (漏洩するとパスワードなしでアカウントに直接アクセスできるセッション cookie) と、
# ログイン中一時的にローカル変数として保持される TOTP コードも対象に含める。
_SENSITIVE_KEYS = {"password", "otp_secret", "code", "ct0", "auth_token"}

# dataclass 等の repr() 文字列 (例: "OnceConfig(password='...', otp_secret='...')")
# および json.dumps() が生成する JSON 文字列 (例: '{"ct0": "...", "auth_token": "..."}')
# 中の機密フィールドをマスクするための正規表現。Sentry SDK は before_send 実行前に
# フレームのローカル変数を dict/list 以外はすべて repr() 済み文字列へ変換するため、
# キーベースの dict 走査だけでは OnceConfig のような dataclass のフィールドや、
# json.dumps() 済みの response body (bytes/str) を検出できない。
# dataclass repr (key='value', キー非クォート, `=` 区切り) と JSON (キーもクォート
# される "key": "value", `:` 区切り、コロン前後の空白は可変) の両方にマッチさせ、
# 置換時は元のキーのクォート有無・区切り文字・空白をそのまま保ち、値部分のみ
# マスクする。
_SENSITIVE_REPR_PATTERN = re.compile(
    r"(?P<keyquote>['\"])?"
    r"(?P<key>" + "|".join(re.escape(key) for key in _SENSITIVE_KEYS) + r")"
    r"(?(keyquote)(?P=keyquote))"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>['\"]).*?(?P=quote)",
    re.IGNORECASE,
)

# キー名一致によるマスク (_SENSITIVE_KEYS) は、patchright 側の `text` 引数のように
# 機密値が異なるキー名に束縛された変数には効かない。そのため `_scrub_repr_string` で
# これらの環境変数の実際の値そのものを文字列中から検索・置換する保険を併用する。
_SENSITIVE_ENV_VARS = ("TWITTER_PASSWORD", "TWITTER_OTP_SECRET")


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
    """スタックトレースのローカル変数と例外メッセージから `_SENSITIVE_KEYS` の値をマスクする。

    Sentry SDK は既定でスタックトレースの各フレームにローカル変数の値を
    そのまま含めて送信するため、この対策がないと do_POST() の except ブロックで
    捕捉した例外経由で生の認証情報が GlitchTip に送信されてしまう。
    breadcrumbs/extra/tags/contexts/request は現状このツールでは使用していないが、
    今後 sentry_sdk.set_context() 等の呼び出しが追加された際に無防備な送信経路が
    残らないよう、存在すれば同様にスクラブする。
    """
    for exception in event.get("exception", {}).get("values", []):
        if isinstance(exception.get("value"), str):
            exception["value"] = _scrub_repr_string(exception["value"])
        frames = exception.get("stacktrace", {}).get("frames", [])
        for frame in frames:
            if "vars" in frame:
                frame["vars"] = _scrub_value(frame["vars"])
    for breadcrumb in event.get("breadcrumbs", {}).get("values", []):
        if "data" in breadcrumb:
            breadcrumb["data"] = _scrub_value(breadcrumb["data"])
        if isinstance(breadcrumb.get("message"), str):
            breadcrumb["message"] = _scrub_repr_string(breadcrumb["message"])
    for key in ("extra", "tags", "contexts", "request"):
        if key in event:
            event[key] = _scrub_value(event[key])
    return event


def _scrub_value(value):
    """dict/list を再帰的に走査し、機密キーの値を "[Filtered]" に置換する。

    dict/list 以外の値 (例: dataclass の repr() 済み文字列) は、キーの一致では
    検出できないため `_scrub_repr_string` によるパターンマッチでマスクする。
    """
    if isinstance(value, dict):
        return {
            key: "[Filtered]" if key.lower() in _SENSITIVE_KEYS else _scrub_value(val)
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_scrub_value(item) for item in value]
    if isinstance(value, str):
        return _scrub_repr_string(value)
    return value


def _scrub_repr_string(text: str) -> str:
    """`key='value'` / `"key": "value"` 形式の機密フィールドと、実際の秘密値そのものをマスクする。

    後者は patchright 側の `text` 引数のように機密値が別名の変数に束縛され、
    `_SENSITIVE_REPR_PATTERN` のキー一致では検出できない場合の保険。
    """
    text = _SENSITIVE_REPR_PATTERN.sub(
        lambda m: (
            f"{m.group('keyquote') or ''}{m.group('key')}{m.group('keyquote') or ''}"
            f"{m.group('sep')}{m.group('quote')}[Filtered]{m.group('quote')}"
        ),
        text,
    )
    for env_var in _SENSITIVE_ENV_VARS:
        secret = os.environ.get(env_var)
        if secret:
            text = text.replace(secret, "[Filtered]")
    return text
