import unittest
from unittest import mock

from evergreen import host_lock, host_transaction


class HostTransactionTests(unittest.TestCase):
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
