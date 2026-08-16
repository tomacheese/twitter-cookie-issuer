"""error_reporting.py の scrub 処理(_scrub_repr_string / _scrub_value / _scrub_before_send)の回帰テスト。

テストで使う ct0/auth_token 等の値はすべてダミー文字列であり、実際の
cookie/token とは一切関係ない。
"""
import json
import unittest

from src.error_reporting import _scrub_before_send, _scrub_repr_string, _scrub_value

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


class TestScrubValueBytes(unittest.TestCase):
    """json.dumps().encode("utf-8") で生成される bytes の response body が scrub されることの確認。"""

    def test_json_bytes_body_is_scrubbed(self):
        payload = {
            "status": "ok",
            "ct0": DUMMY_CT0,
            "auth_token": DUMMY_AUTH_TOKEN,
        }
        body = json.dumps(payload).encode("utf-8")
        result = _scrub_value(body)
        self.assertIsInstance(result, bytes)
        decoded = result.decode("utf-8")
        self.assertNotIn(DUMMY_CT0, decoded)
        self.assertNotIn(DUMMY_AUTH_TOKEN, decoded)
        self.assertIn('"ct0": "[Filtered]"', decoded)
        self.assertIn('"auth_token": "[Filtered]"', decoded)

    def test_non_utf8_bytes_pass_through_unchanged(self):
        binary_value = b"\xff\xfe\x00\x01"
        result = _scrub_value(binary_value)
        self.assertEqual(result, binary_value)

    def test_bytes_without_secrets_pass_through_unchanged(self):
        body = json.dumps({"status": "ok"}).encode("utf-8")
        result = _scrub_value(body)
        self.assertEqual(result, body)


class TestScrubBeforeSendResponseBody(unittest.TestCase):
    """_scrub_before_send が stack-frame locals 中の serialized response body を scrub することの
    end-to-end に近い回帰確認。"""

    def _build_event_with_body_var(self, body):
        return {
            "exception": {
                "values": [
                    {
                        "value": "Broken pipe",
                        "stacktrace": {
                            "frames": [
                                {
                                    "function": "_send_json",
                                    "vars": {"body": body, "status": 200},
                                }
                            ]
                        },
                    }
                ]
            }
        }

    def test_bytes_body_is_scrubbed_from_stack_frame_vars(self):
        body = json.dumps(
            {"status": "ok", "ct0": DUMMY_CT0, "auth_token": DUMMY_AUTH_TOKEN}
        ).encode("utf-8")
        event = self._build_event_with_body_var(body)

        result = _scrub_before_send(event, {})

        scrubbed_body = result["exception"]["values"][0]["stacktrace"]["frames"][0][
            "vars"
        ]["body"]
        self.assertIsInstance(scrubbed_body, bytes)
        decoded = scrubbed_body.decode("utf-8")
        self.assertNotIn(DUMMY_CT0, decoded)
        self.assertNotIn(DUMMY_AUTH_TOKEN, decoded)

    def test_str_body_is_scrubbed_from_stack_frame_vars(self):
        body = json.dumps(
            {"status": "ok", "ct0": DUMMY_CT0, "auth_token": DUMMY_AUTH_TOKEN}
        )
        event = self._build_event_with_body_var(body)

        result = _scrub_before_send(event, {})

        scrubbed_body = result["exception"]["values"][0]["stacktrace"]["frames"][0][
            "vars"
        ]["body"]
        self.assertNotIn(DUMMY_CT0, scrubbed_body)
        self.assertNotIn(DUMMY_AUTH_TOKEN, scrubbed_body)


if __name__ == "__main__":
    unittest.main()
