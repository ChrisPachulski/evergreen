import stat
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from evergreen import host_snapshot


class HostSnapshotTests(unittest.TestCase):
    def test_symlink_snapshot_uses_metadata_after_readlink(self):
        before = SimpleNamespace(
            st_mode=stat.S_IFLNK | 0o777, st_dev=1, st_ino=2, st_nlink=1,
            st_uid=3, st_gid=4, st_atime_ns=10, st_mtime_ns=20,
        )
        after = SimpleNamespace(**{**vars(before), "st_atime_ns": 30})

        with mock.patch.object(host_snapshot.os, "stat", side_effect=(before, after)), \
                mock.patch.object(host_snapshot.os, "readlink", return_value="target"):
            captured = host_snapshot.snapshot_at(Path("/host/link"), 7)

        self.assertEqual(captured.target, "target")
        self.assertIsNone(captured.atime_ns)


if __name__ == "__main__":
    unittest.main()
