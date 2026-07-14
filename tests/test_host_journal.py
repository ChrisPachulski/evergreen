import os
from pathlib import Path
import stat
import subprocess
import sys
from unittest import mock

from evergreen import host_journal, host_transaction
from tests.host_test_support import HostTestCase


ROOT = Path(__file__).resolve().parents[1]

class HostTests(HostTestCase):

    def _recover_only(self, host="codex"):
        from evergreen import hosts

        selected, selection_error = hosts._select(self.home, host)
        self.assertIsNone(selection_error)
        authorization, authorization_error = hosts._authorize_selection(selected)
        self.assertIsNone(authorization_error)
        engine, acquisition_error = host_transaction.TransactionEngine.acquire(
            selected, authorization,
        )
        self.assertIsNone(acquisition_error)
        try:
            errors = engine.recover()
        finally:
            close_error = engine.close()
        self.assertIsNone(close_error)
        return errors

    def _transaction_artifacts(self):
        return sorted(
            path.relative_to(self.home).as_posix()
            for path in self.home.rglob("*")
            if (
                "evergreen-journal" in path.name or
                "evergreen-backup" in path.name or
                path.name.startswith(".evergreen-transaction-")
            )
        )

    def test_crash_before_durable_transaction_commit_rolls_back_every_path(self):
        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        script = f"""
import os
from pathlib import Path
from evergreen import host_transaction, hosts
def crash_before_commit(*args, **kwargs):
    os._exit(95)
host_transaction._write_transaction_commit = crash_before_commit
hosts.install(Path({str(self.home)!r}), Path({str(ROOT)!r}), 'codex')
"""

        crashed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

        self.assertEqual(crashed.returncode, 95, crashed.stderr)
        self.assertTrue(self._transaction_artifacts())
        self.assertFalse(any(
            item.name.startswith(".evergreen-transaction-")
            for item in codex.iterdir()
        ))
        self.assertEqual(self._recover_only(), [])
        self.assertEqual(instructions.read_bytes(), b"original")
        self.assertFalse((codex / ".evergreen-owned.json").exists())
        self.assertFalse((codex / "skills" / "evergreen").exists())
        self.assertEqual(self._transaction_artifacts(), [])

    def test_crash_after_durable_commit_finishes_cleanup_without_rollback(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        script = f"""
import os
from pathlib import Path
from evergreen import host_transaction, hosts
def crash_before_cleanup(*args, **kwargs):
    os._exit(96)
host_transaction._cleanup_committed_entry = crash_before_cleanup
hosts.install(Path({str(self.home)!r}), Path({str(ROOT)!r}), 'codex')
"""

        crashed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

        self.assertEqual(crashed.returncode, 96, crashed.stderr)
        self.assertTrue(any(
            item.name.startswith(".evergreen-transaction-")
            for item in codex.iterdir()
        ))
        self.assertEqual(self._recover_only(), [])
        self.assertIn(hosts.BEGIN_MARKER.encode(), instructions.read_bytes())
        self.assertTrue((codex / ".evergreen-owned.json").is_file())
        self.assertTrue((codex / "skills" / "evergreen").is_symlink())
        self.assertEqual(self._transaction_artifacts(), [])

    def test_committed_transaction_recovery_is_idempotent(self):
        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "AGENTS.md").write_bytes(b"original")
        script = f"""
import os
from pathlib import Path
from evergreen import host_transaction, hosts
host_transaction._cleanup_committed_entry = lambda *args, **kwargs: os._exit(97)
hosts.install(Path({str(self.home)!r}), Path({str(ROOT)!r}), 'codex')
"""
        crashed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

        self.assertEqual(crashed.returncode, 97, crashed.stderr)
        self.assertEqual(self._recover_only(), [])
        recovered = self.snapshot(include_directories=True)
        self.assertEqual(self._recover_only(), [])
        self.assertEqual(self.snapshot(include_directories=True), recovered)
        self.assertEqual(self._transaction_artifacts(), [])

    def test_prepublication_crash_artifacts_are_safely_recovered_on_next_install(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        script = f"""
import os
from pathlib import Path
from evergreen import host_transaction, hosts
real_link = host_transaction.os.link
def crash_after_backup_link(source, destination, *args, **kwargs):
    result = real_link(source, destination, *args, **kwargs)
    if destination.startswith('.AGENTS.md.evergreen-backup-'):
        os._exit(91)
    return result
host_transaction.os.link = crash_after_backup_link
hosts.install(Path({str(self.home)!r}), Path({str(ROOT)!r}), 'codex')
"""

        crashed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

        self.assertEqual(crashed.returncode, 91, crashed.stderr)
        artifacts = sorted(
            path.name for path in codex.iterdir()
            if path.name.startswith(".AGENTS.md.evergreen-")
        )
        self.assertEqual(len(artifacts), 3)
        temporary = next(
            name for name in artifacts
            if "evergreen-backup" not in name and "evergreen-journal" not in name
        )
        backup = next(name for name in artifacts if "evergreen-backup" in name)
        journal = next(name for name in artifacts if "evergreen-journal" in name)
        transaction_ids = {
            temporary.removeprefix(".AGENTS.md.evergreen-"),
            backup.removeprefix(".AGENTS.md.evergreen-backup-"),
            journal.removeprefix(".AGENTS.md.evergreen-journal-"),
        }
        self.assertEqual(len(transaction_ids), 1)
        self.assertEqual(instructions.stat().st_nlink, 2)

        recovered = hosts.install(self.home, ROOT, "codex")

        self.assertTrue(recovered.ok, recovered.messages)
        self.assertEqual(instructions.stat().st_nlink, 1)
        self.assertIn(hosts.BEGIN_MARKER.encode(), instructions.read_bytes())
        self.assertFalse(any(
            path.name.startswith(".AGENTS.md.evergreen-") for path in codex.iterdir()
        ))

    def test_crash_after_initial_regular_create_is_journaled_and_recovered(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        script = f"""
import os
from pathlib import Path
from evergreen import host_transaction, hosts
real_replace = host_transaction.os.replace
def crash_after_publication(source, destination, *args, **kwargs):
    result = real_replace(source, destination, *args, **kwargs)
    if destination == 'AGENTS.md':
        os._exit(92)
    return result
host_transaction.os.replace = crash_after_publication
hosts.install(Path({str(self.home)!r}), Path({str(ROOT)!r}), 'codex')
"""

        crashed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

        self.assertEqual(crashed.returncode, 92, crashed.stderr)
        self.assertTrue(any("evergreen-journal" in item.name for item in codex.iterdir()))
        recovered = hosts.install(self.home, ROOT, "codex")
        self.assertTrue(recovered.ok, recovered.messages)
        self.assertIn(hosts.BEGIN_MARKER.encode(), (codex / "AGENTS.md").read_bytes())
        self.assertFalse(any("evergreen-journal" in item.name for item in codex.iterdir()))

    def test_crash_after_initial_directory_create_is_journaled_and_recovered(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        script = f"""
import os
from pathlib import Path
from evergreen import host_transaction, hosts
real_replace = host_transaction.os.replace
def crash_after_publication(source, destination, *args, **kwargs):
    result = real_replace(source, destination, *args, **kwargs)
    if destination == 'skills':
        os._exit(93)
    return result
host_transaction.os.replace = crash_after_publication
hosts.install(Path({str(self.home)!r}), Path({str(ROOT)!r}), 'codex')
"""
        crashed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

        self.assertEqual(crashed.returncode, 93, crashed.stderr)
        self.assertTrue(any("skills.evergreen-journal" in item.name for item in codex.iterdir()))
        recovered = hosts.install(self.home, ROOT, "codex")
        self.assertTrue(recovered.ok, recovered.messages)
        self.assertTrue((codex / "skills" / "evergreen").is_symlink())
        self.assertFalse(any("skills.evergreen-journal" in item.name for item in codex.iterdir()))

    def test_crash_after_initial_symlink_create_is_journaled_and_recovered(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        script = f"""
import os
from pathlib import Path
from evergreen import host_transaction, hosts
real_replace = host_transaction.os.replace
def crash_after_publication(source, destination, *args, **kwargs):
    result = real_replace(source, destination, *args, **kwargs)
    if destination == 'evergreen':
        os._exit(94)
    return result
host_transaction.os.replace = crash_after_publication
hosts.install(Path({str(self.home)!r}), Path({str(ROOT)!r}), 'codex')
"""
        crashed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

        self.assertEqual(crashed.returncode, 94, crashed.stderr)
        skills = codex / "skills"
        self.assertTrue(any("evergreen-journal" in item.name for item in skills.iterdir()))
        recovered = hosts.install(self.home, ROOT, "codex")
        self.assertTrue(recovered.ok, recovered.messages)
        self.assertTrue((skills / "evergreen").is_symlink())
        self.assertFalse(any("evergreen-journal" in item.name for item in skills.iterdir()))

    def test_unmatched_transaction_artifact_refuses_with_manual_path(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        artifact = codex / (".AGENTS.md.evergreen-" + "a" * 32)
        artifact.write_bytes(b"unmatched")

        result = hosts.install(self.home, ROOT, "codex")

        rendered = " ".join(result.messages)
        self.assertFalse(result.ok)
        self.assertIn(str(artifact), rendered)
        self.assertIn("manual", rendered.lower())
        self.assertTrue(artifact.exists())

    def test_transaction_artifact_scan_is_bounded_and_never_raises(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        for index in range(129):
            transaction_id = f"{index:032x}"
            (codex / f".AGENTS.md.evergreen-journal-{transaction_id}").write_bytes(b"{}")

        result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertIn("artifact scan limit", " ".join(result.messages).lower())
        self.assertEqual(len(list(codex.glob(".AGENTS.md.evergreen-journal-*"))), 129)

    def test_oversized_transaction_journal_is_bounded_and_retained(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        transaction_id = "a" * 32
        journal = codex / f".AGENTS.md.evergreen-journal-{transaction_id}"
        journal.write_bytes(b"x" * (hosts.MAX_STATE_BYTES + 1))

        result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertIn(str(journal), " ".join(result.messages))
        self.assertTrue(journal.exists())

    def test_transaction_artifact_deadline_returns_bounded_diagnostic(self):

        codex = self.home / ".codex"
        codex.mkdir()
        artifact = codex / (".AGENTS.md.evergreen-journal-" + "b" * 32)
        artifact.write_bytes(b"{}")

        descriptor = os.open(codex, os.O_RDONLY)
        try:
            with mock.patch.object(
            host_journal.time, "monotonic", side_effect=[0, 10]
            ):
                error = host_journal.recover_target_artifacts(
                    descriptor, codex / "AGENTS.md",
                )
        finally:
            os.close(descriptor)

        self.assertIn("artifact scan limit", error.lower())
        self.assertTrue(artifact.exists())

    def test_recovery_oserror_returns_operation_result_and_releases_lock(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        with mock.patch.object(
            host_transaction.TransactionEngine, "recover",
            side_effect=OSError("injected recovery failure"),
        ):
            result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertIn("recovery failed", " ".join(result.messages).lower())
        retried = hosts.install(self.home, ROOT, "codex")
        self.assertTrue(retried.ok, retried.messages)

    def test_corrupt_backup_is_retained_and_never_restored(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        real_action = host_transaction._perform_action

        def corrupt_backup_then_fail(action, path, value, *args, **kwargs):
            postimage = real_action(action, path, value, *args, **kwargs)
            if path.name == instructions.name:
                backup = next(
                    item for item in codex.iterdir() if "evergreen-backup" in item.name
                )
                backup.write_bytes(b"corrupt backup")
                raise OSError("force rollback with corrupt backup")
            return postimage

        with mock.patch.object(
            host_transaction, "_perform_action", side_effect=corrupt_backup_then_fail
        ):
            result = hosts.install(self.home, ROOT, "codex")

        backups = [item for item in codex.iterdir() if "evergreen-backup" in item.name]
        rendered = " ".join(result.messages)
        self.assertFalse(result.ok)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"corrupt backup")
        self.assertIn(hosts.BEGIN_MARKER.encode(), instructions.read_bytes())
        self.assertIn(str(backups[0]), rendered)
        self.assertIn("manual recovery", rendered.lower())
        self.assertNotIn("ordinary recovery completed", rendered)

    def test_xattr_corrupt_backup_is_retained_and_never_restored(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        attribute = self.set_test_xattr(instructions, "backup-integrity", b"original")
        if attribute is None:
            self.skipTest("extended attributes unavailable")
        real_action = host_transaction._perform_action

        def corrupt_backup_then_fail(action, path, value, *args, **kwargs):
            postimage = real_action(action, path, value, *args, **kwargs)
            if path.name == instructions.name:
                backup = next(
                    item for item in codex.iterdir() if "evergreen-backup" in item.name
                )
                changed = self.set_test_xattr(backup, "backup-integrity", b"corrupt")
                self.assertEqual(changed, attribute)
                raise OSError("force rollback with corrupt backup metadata")
            return postimage

        with mock.patch.object(
            host_transaction, "_perform_action", side_effect=corrupt_backup_then_fail
        ):
            result = hosts.install(self.home, ROOT, "codex")

        backups = [item for item in codex.iterdir() if "evergreen-backup" in item.name]
        self.assertFalse(result.ok)
        self.assertEqual(len(backups), 1)
        self.assertEqual(self.get_test_xattr(backups[0], attribute), b"corrupt")
        self.assertIn(hosts.BEGIN_MARKER.encode(), instructions.read_bytes())

    def test_real_unlink_then_raise_is_resolved_during_backup_cleanup(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        installed = hosts.install(self.home, ROOT, "codex")
        self.assertTrue(installed.ok, installed.messages)
        ownership = codex / hosts.OWNERSHIP_FILE
        real_unlink = hosts.os.unlink
        raised = False

        def unlink_then_raise(path, *args, **kwargs):
            nonlocal raised
            result = real_unlink(path, *args, **kwargs)
            if ownership.name in os.fspath(path) and "evergreen-backup" in os.fspath(path) and not raised:
                raised = True
                raise OSError("ambiguous unlink result")
            return result

        with mock.patch.object(hosts.os, "unlink", side_effect=unlink_then_raise):
            result = hosts.uninstall(self.home, "codex")

        self.assertTrue(result.ok, result.messages)
        self.assertFalse(ownership.exists())
        self.assertFalse(any("evergreen-backup" in item.name for item in codex.iterdir()))

    def test_backup_unlink_fsync_failure_never_claims_backup_is_retained(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "AGENTS.md").write_bytes(b"original")
        real_unlink = hosts.os.unlink
        real_fsync = hosts.os.fsync
        backup_removed = False
        failed = False

        def observe_unlink(path, *args, **kwargs):
            nonlocal backup_removed
            result = real_unlink(path, *args, **kwargs)
            if "evergreen-backup" in os.fspath(path):
                backup_removed = True
            return result

        def fail_directory_fsync(descriptor):
            nonlocal failed
            if backup_removed and not failed and stat.S_ISDIR(os.fstat(descriptor).st_mode):
                failed = True
                raise OSError("injected directory durability failure")
            return real_fsync(descriptor)

        with (
            mock.patch.object(hosts.os, "unlink", side_effect=observe_unlink),
            mock.patch.object(hosts.os, "fsync", side_effect=fail_directory_fsync),
        ):
            result = hosts.install(self.home, ROOT, "codex")

        rendered = " ".join(result.messages).lower()
        self.assertFalse(result.ok)
        self.assertIn("removal succeeded", rendered)
        self.assertNotIn("backup retained", rendered)
        self.assertFalse(any("evergreen-backup" in item.name for item in codex.iterdir()))
