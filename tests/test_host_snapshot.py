import stat
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from evergreen import host_snapshot
from evergreen.host_types import HostStatus


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

    def test_capture_binds_managed_root_link_to_resolved_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            managed = home / "managed"
            (managed / "skills").mkdir(parents=True)
            root = home / ".claude"
            root.symlink_to(managed, target_is_directory=True)
            resolved = managed.resolve()
            status = HostStatus(
                name="claude", present=True, root=root, resolved_root=resolved,
                instructions=resolved / "CLAUDE.md",
                skill=resolved / "skills" / "evergreen",
                ownership=resolved / ".evergreen-owned.json",
            )

            captured = host_snapshot.capture_preflight([status])

            self.assertEqual(captured[root].kind, "symlink")
            self.assertEqual(captured[resolved].kind, "directory")

    def test_capture_refuses_changed_managed_root_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            managed = home / "managed"
            replacement = home / "replacement"
            (managed / "skills").mkdir(parents=True)
            replacement.mkdir()
            root = home / ".claude"
            root.symlink_to(replacement, target_is_directory=True)
            resolved = managed.resolve()
            status = HostStatus(
                name="claude", present=True, root=root, resolved_root=resolved,
                instructions=resolved / "CLAUDE.md",
                skill=resolved / "skills" / "evergreen",
                ownership=resolved / ".evergreen-owned.json",
            )

            with self.assertRaisesRegex(OSError, "managed host root changed"):
                host_snapshot.capture_preflight([status])


if __name__ == "__main__":
    unittest.main()
