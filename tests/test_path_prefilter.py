from pathlib import Path
import tempfile
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


if __name__ == "__main__":
    unittest.main()
