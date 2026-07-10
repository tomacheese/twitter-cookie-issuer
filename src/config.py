"""once モードの環境変数からの設定読み込みを行うモジュール。"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# once モードの出力先。daemon モードは main.py 側でユーザー名ごとのパスを組み立てる。
COOKIES_PATH = Path("/data/cookies.json")
SCREENSHOTS_DIR = Path("/data/screenshots")


class ConfigError(RuntimeError):
    """設定不備を表す例外。"""


@dataclass
class OnceConfig:
    """once モードでのログインに必要な設定値。"""

    username: str
    password: str
    email: str | None
    otp_secret: str | None


def load_once_config() -> OnceConfig:
    """環境変数から once モード用の設定を読み込む。

    Raises:
        ConfigError: TWITTER_USERNAME / TWITTER_PASSWORD が未設定の場合。
    """
    username = os.environ.get("TWITTER_USERNAME")
    password = os.environ.get("TWITTER_PASSWORD")
    if not username or not password:
        raise ConfigError(
            "TWITTER_USERNAME / TWITTER_PASSWORD が設定されていません。"
        )
    return OnceConfig(
        username=username,
        password=password,
        email=os.environ.get("TWITTER_EMAIL"),
        otp_secret=os.environ.get("TWITTER_OTP_SECRET"),
    )
