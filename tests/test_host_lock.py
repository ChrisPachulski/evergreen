import unittest
from unittest import mock
from pathlib import Path
import tempfile

from evergreen import host_lock, host_snapshot, host_transaction, hosts


ROOT = Path(__file__).resolve().parents[1]


class HostTransactionTests(unittest.TestCase):
    def test_lock_exclusivity_survives_host_directory_rename_and_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            codex = home / ".codex"
            codex.mkdir()
            selected = [hosts.detect_hosts(home)[1]]
            authorization = host_snapshot.capture_authorization(selected)
            first, first_error = host_transaction.TransactionEngine.acquire(
                selected, authorization
            )
            self.assertIsNone(first_error)
            displaced = home / "displaced-codex"
            codex.rename(displaced)
            codex.mkdir()
            replacement_selected = [hosts.detect_hosts(home)[1]]
            replacement_authorization = host_snapshot.capture_authorization(
                replacement_selected
            )
            try:
                second, second_error = host_transaction.TransactionEngine.acquire(
                    replacement_selected, replacement_authorization
                )
                if second is not None:
                    second.close()
            finally:
                first.close()

            self.assertIsNone(second)
            self.assertIn("another host operation", second_error)

    def test_release_attempts_every_unlock_and_close_in_reverse_order(self):
        unlocked, closed = [], []

        def flock(descriptor, _operation):
            unlocked.append(descriptor)
            if descriptor == 2:
                raise OSError("unlock failed")

        def close(descriptor):
            closed.append(descriptor)
            if descriptor == 1:
                raise OSError("close failed")

        with mock.patch.object(host_lock.fcntl, "flock", side_effect=flock), \
                mock.patch.object(host_lock.os, "close", side_effect=close):
            errors = host_lock.release([1, 2, 3])

        self.assertEqual(unlocked, [3, 2, 1])
        self.assertEqual(closed, [3, 2, 1])
        self.assertEqual(len(errors), 2)

    def test_engine_close_is_idempotent_and_normalizes_cleanup_errors(self):
        engine = host_transaction.TransactionEngine((), [1, 2])
        with mock.patch.object(host_transaction, "_unlock_hosts", return_value=["failure"]) as release:
            first = engine.close()
            second = engine.close()
        self.assertIn("cleanup failed", first)
        self.assertIsNone(second)
        self.assertEqual(release.call_args_list, [mock.call([1, 2])])

    def test_public_operations_return_cleanup_errors_and_release_real_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".codex").mkdir()
            real_release = host_lock.release

            def release_with_diagnostic(descriptors):
                return real_release(descriptors) + ["injected cleanup failure"]

            with mock.patch.object(
                host_transaction, "_unlock_hosts", side_effect=release_with_diagnostic,
            ):
                installed = hosts.install(home, ROOT, "codex")
            self.assertFalse(installed.ok)
            self.assertIn("cleanup failed", " ".join(installed.messages))

            with mock.patch.object(
                host_transaction, "_unlock_hosts", side_effect=release_with_diagnostic,
            ):
                removed = hosts.uninstall(home, "codex")
            self.assertFalse(removed.ok)
            self.assertIn("cleanup failed", " ".join(removed.messages))

            reacquired = hosts.install(home, ROOT, "codex", dry_run=True)
            self.assertTrue(reacquired.ok, reacquired.messages)
