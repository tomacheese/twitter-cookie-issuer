"""error_reporting.py の scrub 処理(_scrub_repr_string / _scrub_value / _scrub_before_send)の回帰テスト。

テストで使う ct0/auth_token 等の値はすべてダミー文字列であり、実際の
cookie/token とは一切関係ない。
"""
import unittest

from src.error_reporting import _scrub_repr_string

DUMMY_CT0 = "dummy-ct0-value"
DUMMY_AUTH_TOKEN = "dummy-auth-token-value"


class TestScrubReprStringDataclassFormat(unittest.TestCase):
    """既存の dataclass repr() 形式 (key='value') が引き続き scrub されることの回帰確認。"""

    def test_dataclass_repr_style_is_scrubbed(self):
        text = f"OnceConfig(ct0='{DUMMY_CT0}', auth_token='{DUMMY_AUTH_TOKEN}')"
        result = _scrub_repr_string(text)
        self.assertNotIn(DUMMY_CT0, result)
        self.assertNotIn(DUMMY_AUTH_TOKEN, result)
        self.assertIn("ct0='[Filtered]'", result)
        self.assertIn("auth_token='[Filtered]'", result)


class TestScrubReprStringJsonFormat(unittest.TestCase):
    """json.dumps() が生成する "key": "value" 形式が scrub されることの確認。"""

    def test_json_style_with_space_after_colon_is_scrubbed(self):
        text = f'{{"status": "ok", "ct0": "{DUMMY_CT0}", "auth_token": "{DUMMY_AUTH_TOKEN}"}}'
        result = _scrub_repr_string(text)
        self.assertNotIn(DUMMY_CT0, result)
        self.assertNotIn(DUMMY_AUTH_TOKEN, result)
        self.assertIn('"ct0": "[Filtered]"', result)
        self.assertIn('"auth_token": "[Filtered]"', result)

    def test_json_style_without_space_after_colon_is_scrubbed(self):
        text = f'{{"ct0":"{DUMMY_CT0}","auth_token":"{DUMMY_AUTH_TOKEN}"}}'
        result = _scrub_repr_string(text)
        self.assertNotIn(DUMMY_CT0, result)
        self.assertNotIn(DUMMY_AUTH_TOKEN, result)
        self.assertIn('"ct0":"[Filtered]"', result)
        self.assertIn('"auth_token":"[Filtered]"', result)


if __name__ == "__main__":
    unittest.main()
