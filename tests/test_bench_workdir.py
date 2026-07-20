"""Tests for eval/bench/workdir.py — the canonical EVERGREEN_WORK_DIR resolver.

Run: python3 -m pytest tests/test_bench_workdir.py -q
"""
import shutil
import tempfile
import unittest
from pathlib import Path

from eval.bench.workdir import work_dir, work_root


class WorkRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_path, True)

    def test_explicit_work_dir_env_wins(self):
        chosen = self.tmp_path / "explicit-root"
        environ = {"EVERGREEN_WORK_DIR": str(chosen), "XDG_DATA_HOME": str(self.tmp_path / "xdg")}
        self.assertEqual(work_root(environ, home=self.tmp_path), chosen)

    def test_xdg_data_home_used_when_work_dir_unset(self):
        xdg = self.tmp_path / "xdg"
        environ = {"XDG_DATA_HOME": str(xdg)}
        self.assertEqual(work_root(environ, home=self.tmp_path), xdg / "evergreen")

    def test_falls_back_to_home_local_share_when_nothing_set(self):
        environ = {}
        self.assertEqual(
            work_root(environ, home=self.tmp_path),
            self.tmp_path / ".local" / "share" / "evergreen",
        )

    def test_empty_string_work_dir_is_treated_as_unset(self):
        environ = {"EVERGREEN_WORK_DIR": "", "XDG_DATA_HOME": str(self.tmp_path / "xdg")}
        self.assertEqual(
            work_root(environ, home=self.tmp_path), self.tmp_path / "xdg" / "evergreen"
        )


class WorkDirTests(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_path, True)

    def test_returns_root_purpose_when_no_legacy_dir_exists(self):
        environ = {"EVERGREEN_WORK_DIR": str(self.tmp_path / "root")}
        result = work_dir("cascade", environ=environ, home=self.tmp_path)
        self.assertEqual(result, self.tmp_path / "root" / "cascade")

    def test_returns_legacy_dir_when_it_exists(self):
        legacy = self.tmp_path / "evergreen-cascade"
        legacy.mkdir()
        environ = {"EVERGREEN_WORK_DIR": str(self.tmp_path / "root")}
        result = work_dir("cascade", environ=environ, home=self.tmp_path)
        self.assertEqual(result, legacy)

    def test_invalid_purpose_raises_value_error(self):
        environ = {"EVERGREEN_WORK_DIR": str(self.tmp_path / "root")}
        for bad in ("Cascade", "1cascade", "cascade_gate", "", "cascade gate"):
            with self.subTest(purpose=bad):
                with self.assertRaises(ValueError):
                    work_dir(bad, environ=environ, home=self.tmp_path)

    def test_trailing_newline_purpose_raises_value_error(self):
        # Python's `$` in re also matches just before a trailing "\n", so a
        # `.match()`-based check would wrongly accept this. Must use fullmatch.
        environ = {"EVERGREEN_WORK_DIR": str(self.tmp_path / "root")}
        for bad in ("cascade\n", "a\nb"):
            with self.subTest(purpose=bad):
                with self.assertRaises(ValueError):
                    work_dir(bad, environ=environ, home=self.tmp_path)

    def test_no_directories_are_created(self):
        environ = {"EVERGREEN_WORK_DIR": str(self.tmp_path / "root")}
        work_dir("cascade", environ=environ, home=self.tmp_path)
        self.assertFalse((self.tmp_path / "root").exists())
        self.assertFalse((self.tmp_path / "root" / "cascade").exists())


if __name__ == "__main__":
    unittest.main()
