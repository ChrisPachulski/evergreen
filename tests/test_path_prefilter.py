from pathlib import Path
import os
import tempfile
import time
import unittest
from unittest import mock

from ci import path_prefilter


class PathPrefilterTests(unittest.TestCase):
    def test_tiny_path_fanout_stops_at_work_bound(self):
        with tempfile.TemporaryDirectory() as temporary:
            listing = Path(temporary) / "paths"
            listing.write_bytes(b"a\0b\0c\0")

            matched, error = path_prefilter.classify(
                listing, "docs", max_bytes=100, max_paths=2, timeout_seconds=1
            )

        self.assertIsNone(matched)
        self.assertIn("2 paths", error)

    def test_path_scan_stops_at_deadline(self):
        with tempfile.TemporaryDirectory() as temporary:
            listing = Path(temporary) / "paths"
            listing.write_bytes(b"a\0")
            with mock.patch.object(path_prefilter.time, "monotonic", side_effect=[0, 2]):
                matched, error = path_prefilter.classify(
                    listing, "docs", max_bytes=100, max_paths=2, timeout_seconds=1
                )

        self.assertIsNone(matched)
        self.assertIn("wall-clock", error)

    def test_rejects_paths_outside_the_shared_protocol_policy(self):
        invalid = (
            "../escape.py", "/absolute.py", r"back\slash.py", "line\nbreak.py",
            "carriage\rreturn.py", "C:/absolute.py", "docs//bad.py", "a" * 1025,
        )
        for value in invalid:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as temporary:
                listing = Path(temporary) / "paths"
                listing.write_bytes(value.encode("utf-8") + b"\0")
                matched, error = path_prefilter.classify(
                    listing, "code", max_bytes=4096, max_paths=2, timeout_seconds=1
                )

            self.assertIsNone(matched)
            self.assertIn("not citable", error)

    def test_inner_elapsed_limit_is_detected_after_blocking_read_returns(self):
        with tempfile.TemporaryDirectory() as temporary:
            listing = Path(temporary) / "paths"
            listing.write_bytes(b"a.py\0")
            real_read = os.read

            def slow_read(descriptor, size):
                time.sleep(0.05)
                return real_read(descriptor, size)

            started = time.monotonic()
            with mock.patch("os.read", side_effect=slow_read):
                matched, error = path_prefilter.classify(
                    listing, "code", max_bytes=100, max_paths=2,
                    timeout_seconds=0.01,
                )

        self.assertIsNone(matched)
        self.assertIn("wall-clock", error)
        self.assertGreaterEqual(time.monotonic() - started, 0.05)

    def test_rejects_input_replaced_between_lstat_and_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            listing = root / "paths"
            target = root / "target"
            listing.write_bytes(b"a.py\0")
            target.write_bytes(b"escape.py\0")
            real_open = os.open
            replaced = False

            def replace_then_open(path, flags):
                nonlocal replaced
                if not replaced:
                    replaced = True
                    listing.unlink()
                    listing.symlink_to(target)
                return real_open(path, flags)

            with mock.patch("os.open", side_effect=replace_then_open):
                matched, error = path_prefilter.classify(
                    listing, "code", max_bytes=100, max_paths=2,
                    timeout_seconds=1,
                )

        self.assertIsNone(matched)
        self.assertIn("could not be read", error)


if __name__ == "__main__":
    unittest.main()
