import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest import mock

from evergreen import host_journal
from evergreen.host_snapshot import snapshot
from evergreen.host_types import (
    JournalPhase, JournalRecord, MutationKind, PathSnapshot, TransactionCommit,
)


ROOT = Path(__file__).resolve().parents[1]


class HostJournalTests(unittest.TestCase):
    def test_control_json_rejects_duplicates_nonfinite_and_oversized_payloads(self):
        transaction_id = "a" * 32
        valid = TransactionCommit(
            1, transaction_id, ("codex",), ("codex",),
        ).encode()
        duplicate = valid.replace(
            b'"phase":"committed"',
            b'"phase":"committed","phase":"committed"',
        )
        nonfinite = valid.replace(b'"schema_version":1', b'"schema_version":NaN')
        malformed_participants = valid.replace(
            b'"participants":["codex"]', b'"participants":[[]]',
        )
        for payload in (
            duplicate, nonfinite, malformed_participants,
            b"{" + b" " * 4096 + b"}",
        ):
            with self.subTest(payload=payload[:32]):
                with self.assertRaises(ValueError):
                    TransactionCommit.parse(payload)

        target = Path("target")
        record = JournalRecord(
            1, transaction_id, JournalPhase.PUBLISHED,
            MutationKind.CREATE_REGULAR, target.name, None, None,
            f".{target.name}.evergreen-journal-{transaction_id}",
            PathSnapshot(target, "absent").journal_identity(),
            PathSnapshot(target, "absent").journal_identity(),
        ).encode()
        with self.assertRaises(ValueError):
            JournalRecord.parse(record.replace(
                b'"schema_version":1', b'"schema_version":Infinity',
            ))

    def test_marker_scan_has_a_hard_count_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            for index in range(host_journal.MAX_TRANSACTION_MARKERS + 1):
                transaction_id = f"{index:032x}"
                marker = parent / host_journal.transaction_commit_name(transaction_id)
                marker.write_bytes(TransactionCommit(
                    1, transaction_id, ("codex",), ("codex",),
                ).encode())
                marker.chmod(0o600)
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                with self.assertRaises(OSError):
                    host_journal.read_transaction_commits(descriptor)
            finally:
                os.close(descriptor)

    def test_marker_metadata_rejects_mode_hardlink_and_foreign_owner(self):
        for case in ("mode", "hardlink", "owner"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                transaction_id = "b" * 32
                marker = parent / host_journal.transaction_commit_name(transaction_id)
                marker.write_bytes(TransactionCommit(
                    1, transaction_id, ("codex",), ("codex",),
                ).encode())
                marker.chmod(0o600)
                if case == "mode":
                    marker.chmod(0o644)
                elif case == "hardlink":
                    os.link(marker, parent / "marker-alias")
                descriptor = os.open(parent, os.O_RDONLY)
                getuid = (
                    mock.patch.object(host_journal.os, "getuid", return_value=os.getuid() + 1)
                    if case == "owner" else mock.patch.object(
                        host_journal.os, "getuid", wraps=os.getuid,
                    )
                )
                try:
                    with getuid, self.assertRaises(ValueError):
                        host_journal.read_transaction_commits(descriptor)
                finally:
                    os.close(descriptor)

    def test_marker_writer_never_deletes_a_preexisting_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            transaction_id = "d" * 32
            update = parent / host_journal.transaction_commit_update_name(
                transaction_id,
            )
            update.write_bytes(b"preexisting")
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                with self.assertRaises(FileExistsError):
                    host_journal.write_transaction_commit(
                        descriptor, transaction_id, ("codex",),
                    )
            finally:
                os.close(descriptor)
            self.assertEqual(update.read_bytes(), b"preexisting")

    def test_journal_metadata_rejects_mode_hardlink_and_foreign_owner(self):
        for case in ("mode", "hardlink", "owner"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                target = parent / "target"
                target.write_bytes(b"after")
                after = snapshot(target)
                transaction_id = "c" * 32
                journal = f".{target.name}.evergreen-journal-{transaction_id}"
                record = JournalRecord(
                    1, transaction_id, JournalPhase.PUBLISHED,
                    MutationKind.CREATE_REGULAR, target.name, None, None, journal,
                    PathSnapshot(target, "absent").journal_identity(),
                    after.journal_identity(),
                )
                descriptor = os.open(parent, os.O_RDONLY)
                try:
                    host_journal.write_journal_at(
                        descriptor, journal, record, create=True,
                    )
                    if case == "mode":
                        (parent / journal).chmod(0o644)
                    elif case == "hardlink":
                        os.link(parent / journal, parent / "journal-alias")
                    getuid = (
                        mock.patch.object(
                            host_journal.os, "getuid", return_value=os.getuid() + 1,
                        )
                        if case == "owner" else mock.patch.object(
                            host_journal.os, "getuid", wraps=os.getuid,
                        )
                    )
                    with getuid:
                        error = host_journal.recover_target_artifacts(
                            descriptor, target,
                        )
                finally:
                    os.close(descriptor)
                self.assertIn("manual recovery", error)
                self.assertTrue((parent / journal).exists())
    def test_prepared_replace_cleanup_survives_second_crash(self):
        for mutation in (MutationKind.REPLACE_REGULAR, MutationKind.REPLACE_SYMLINK):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                target = parent / "target"
                kind = mutation.value.rsplit("_", 1)[1]
                self._make_kind(target, kind, b"before")
                before = snapshot(target)
                transaction_id = uuid.uuid4().hex
                temporary = f".{target.name}.evergreen-{transaction_id}"
                backup = f".{target.name}.evergreen-backup-{transaction_id}"
                journal = f".{target.name}.evergreen-journal-{transaction_id}"
                os.link(target, parent / backup, follow_symlinks=False)
                self._make_kind(parent / temporary, kind, b"after")
                staged = snapshot(parent / temporary)
                after = PathSnapshot(
                    target, staged.kind, data=staged.data, target=staged.target,
                    mode=staged.mode, dev=staged.dev, ino=staged.ino, nlink=staged.nlink,
                    uid=staged.uid, gid=staged.gid, atime_ns=staged.atime_ns,
                    mtime_ns=staged.mtime_ns, metadata_digest=staged.metadata_digest,
                )
                record = JournalRecord(
                    1, transaction_id, JournalPhase.PREPARED, mutation, target.name,
                    temporary, backup, journal, before.journal_identity(),
                    after.journal_identity(),
                )
                descriptor = os.open(parent, os.O_RDONLY)
                try:
                    host_journal.write_journal_at(descriptor, journal, record, create=True)
                finally:
                    os.close(descriptor)
                script = f"""
import os
from pathlib import Path
from evergreen import host_journal
parent, target = Path({str(parent)!r}), Path({str(target)!r})
real_remove = host_journal.remove_kind
def remove_then_crash(parent_fd, name, kind):
    real_remove(parent_fd, name, kind)
    if name == {temporary!r}:
        os._exit(78)
host_journal.remove_kind = remove_then_crash
descriptor = os.open(parent, os.O_RDONLY)
host_journal.recover_target_artifacts(descriptor, target)
"""
                crashed = subprocess.run([sys.executable, "-c", script], cwd=ROOT)
                self.assertEqual(crashed.returncode, 78)
                descriptor = os.open(parent, os.O_RDONLY)
                try:
                    error = host_journal.recover_target_artifacts(descriptor, target)
                finally:
                    os.close(descriptor)
                self.assertIsNone(error)
                self.assertEqual(snapshot(target).journal_identity(), before.journal_identity())
                self.assertFalse(any("evergreen-" in path.name for path in parent.iterdir()))

    def test_interrupted_journal_phase_update_is_recovered_without_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target = parent / "target"
            target.write_bytes(b"after")
            after = snapshot(target)
            transaction_id = uuid.uuid4().hex
            journal = f".{target.name}.evergreen-journal-{transaction_id}"
            record = JournalRecord(
                1, transaction_id, JournalPhase.PUBLISHED,
                MutationKind.CREATE_REGULAR, target.name, None, None, journal,
                PathSnapshot(target, "absent").journal_identity(),
                after.journal_identity(),
            )
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                host_journal.write_journal_at(descriptor, journal, record, create=True)
            finally:
                os.close(descriptor)
            script = f"""
import os
from pathlib import Path
from evergreen import host_journal
parent, target = Path({str(parent)!r}), Path({str(target)!r})
real_replace = host_journal.os.replace
def crash(*args, **kwargs):
    os._exit(79)
host_journal.os.replace = crash
descriptor = os.open(parent, os.O_RDONLY)
host_journal.recover_target_artifacts(descriptor, target)
"""
            crashed = subprocess.run([sys.executable, "-c", script], cwd=ROOT)
            self.assertEqual(crashed.returncode, 79)
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                error = host_journal.recover_target_artifacts(descriptor, target)
            finally:
                os.close(descriptor)
            self.assertIsNone(error)
            self.assertFalse(target.exists())
            self.assertEqual(list(parent.iterdir()), [])

    def test_many_unrelated_entries_do_not_consume_artifact_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            target = parent / "AGENTS.md"
            for index in range(1000):
                (parent / f"ordinary-{index}").touch()
            descriptor = os.open(parent, os.O_RDONLY)
            try:
                self.assertIsNone(host_journal.recover_target_artifacts(descriptor, target))
            finally:
                os.close(descriptor)

    def test_recovery_crash_is_idempotent_for_every_mutation_kind(self):
        for mutation in MutationKind:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                parent = Path(directory)
                target = parent / "target"
                transaction_id = uuid.uuid4().hex
                journal_name = f".{target.name}.evergreen-journal-{transaction_id}"
                backup_name = f".{target.name}.evergreen-backup-{transaction_id}"
                before = self._make_before(target, mutation)
                backup = None
                if mutation.value.startswith(("replace_", "delete_")):
                    os.replace(target, parent / backup_name)
                    backup = backup_name
                if mutation.value.startswith(("create_", "replace_")):
                    self._make_kind(target, mutation.value.rsplit("_", 1)[1], b"after")
                after = snapshot(target, allow_directory=True) if target.exists() or target.is_symlink() \
                    else PathSnapshot(target, "absent")
                record = JournalRecord(
                    1, transaction_id, JournalPhase.PUBLISHED, mutation, target.name,
                    None, backup, journal_name, before.journal_identity(), after.journal_identity(),
                )
                descriptor = os.open(parent, os.O_RDONLY)
                try:
                    host_journal.write_journal_at(
                        descriptor, journal_name, record, create=True,
                    )
                finally:
                    os.close(descriptor)

                script = f"""
import os
from pathlib import Path
from evergreen import host_journal
parent, target = Path({str(parent)!r}), Path({str(target)!r})
real_unlink = host_journal.os.unlink
def crash(name, *args, **kwargs):
    if name == {journal_name!r}:
        os._exit(77)
    return real_unlink(name, *args, **kwargs)
host_journal.os.unlink = crash
descriptor = os.open(parent, os.O_RDONLY)
host_journal.recover_target_artifacts(descriptor, target)
"""
                crashed = subprocess.run([sys.executable, "-c", script], cwd=ROOT)
                self.assertEqual(crashed.returncode, 77)
                descriptor = os.open(parent, os.O_RDONLY)
                try:
                    error = host_journal.recover_target_artifacts(descriptor, target)
                finally:
                    os.close(descriptor)
                self.assertIsNone(error)
                self.assertFalse((parent / journal_name).exists())
                self.assertFalse((parent / backup_name).exists())
                if mutation.value.startswith("create_"):
                    self.assertFalse(target.exists() or target.is_symlink())
                else:
                    restored = snapshot(target, allow_directory=True)
                    self.assertEqual(restored.kind, before.kind)
                    self.assertEqual(restored.data, before.data)
                    self.assertEqual(restored.target, before.target)

    def _make_before(self, target, mutation):
        if mutation.value.startswith("create_"):
            return PathSnapshot(target, "absent")
        self._make_kind(target, mutation.value.rsplit("_", 1)[1], b"before")
        return snapshot(target, allow_directory=True)

    def _make_kind(self, target, kind, data):
        if kind == "regular":
            target.write_bytes(data)
        elif kind == "symlink":
            target.symlink_to(data.decode())
        elif kind == "directory":
            target.mkdir()
        else:
            raise AssertionError(kind)
