"""_host_diagnostics() の回帰テスト。"""
import time
import unittest
from unittest.mock import patch

from src.login import _host_diagnostics


class TestHostDiagnostics(unittest.TestCase):
    def test_returns_all_keys(self):
        d = _host_diagnostics(time.monotonic())
        self.assertEqual(set(d), {"elapsed_s", "loadavg", "mem_available_mb"})

    def test_failures_yield_none(self):
        with patch("os.getloadavg", side_effect=OSError), patch(
            "builtins.open", side_effect=OSError
        ):
            d = _host_diagnostics(time.monotonic())
        self.assertIsNone(d["loadavg"])
        self.assertIsNone(d["mem_available_mb"])


if __name__ == "__main__":
    unittest.main()
