"""cookie キャッシュの file / directory が owner 専用 mode で作られることの回帰テスト。"""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from src.login import _write_cache


class TestWriteCachePermissions(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._old_umask = os.umask(0o022)
        self.addCleanup(os.umask, self._old_umask)

    def _mode(self, path: Path) -> int:
        return stat.S_IMODE(path.stat().st_mode)

    def test_new_cache_file_and_directory_are_owner_only(self):
        cache_path = self.root / "cookies" / "user.json"
        _write_cache(cache_path, cookies={"ct0": "x"})
        self.assertEqual(self._mode(cache_path), 0o600)
        self.assertEqual(self._mode(cache_path.parent), 0o700)

    def test_existing_loose_cache_file_is_tightened_on_rewrite(self):
        cache_path = self.root / "user.json"
        cache_path.write_text(json.dumps({"ct0": "old"}), encoding="utf-8")
        cache_path.chmod(0o644)
        _write_cache(cache_path, cookies={"ct0": "new"})
        self.assertEqual(self._mode(cache_path), 0o600)
        self.assertEqual(json.loads(cache_path.read_text(encoding="utf-8")), {"ct0": "new"})

    def test_stale_loose_tmp_file_does_not_leak_its_mode(self):
        cache_path = self.root / "user.json"
        tmp_path = cache_path.with_name("user.json.tmp")
        tmp_path.write_text("stale", encoding="utf-8")
        tmp_path.chmod(0o644)
        _write_cache(cache_path, cookies={"ct0": "x"})
        self.assertEqual(self._mode(cache_path), 0o600)


if __name__ == "__main__":
    unittest.main()
