import ctypes
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from evergreen import host_metadata


ROOT = Path(__file__).resolve().parents[1]


class HostMetadataTests(unittest.TestCase):
    def test_deadline_crossing_inside_final_value_fetch_is_rejected(self):
        clock = [0]

        class Function:
            def __init__(self, implementation):
                self.implementation = implementation

            def __call__(self, *args):
                return self.implementation(*args)

        class Library:
            pass

        library = Library()
        list_calls = 0

        def list_attributes(_descriptor, buffer, _size):
            nonlocal list_calls
            list_calls += 1
            if buffer is None:
                return 2
            buffer.raw = b"a\0"
            return 2

        get_calls = 0

        def get_attribute(_descriptor, _name, buffer, _size):
            nonlocal get_calls
            get_calls += 1
            if buffer is None:
                return 1
            buffer[0] = b"x"
            clock[0] = 2
            return 1

        library.flistxattr = Function(list_attributes)
        library.fgetxattr = Function(get_attribute)
        with mock.patch.object(host_metadata.sys, "platform", "linux"), \
                mock.patch.object(host_metadata.ctypes, "CDLL", return_value=library), \
                mock.patch.object(host_metadata.time, "monotonic", side_effect=lambda: clock[0]):
            with self.assertRaises(TimeoutError):
                host_metadata._items_fd(1, deadline=1)
        self.assertEqual(get_calls, 2)

    def test_acl_free_has_pointer_safe_macos_signature(self):
        library = ctypes.CDLL(None)
        if not hasattr(library, "acl_free"):
            self.skipTest("ACL API unavailable")
        host_metadata._configure_acl_api(library)
        self.assertEqual(library.acl_free.argtypes, [ctypes.c_void_p])
        self.assertIs(library.acl_free.restype, ctypes.c_int)

    def test_expired_deadline_stops_before_metadata_enumeration(self):
        with tempfile.TemporaryDirectory() as directory:
            descriptor = os.open(Path(directory) / "file", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                with self.assertRaises(TimeoutError):
                    host_metadata.digest_fd(descriptor, deadline=time.monotonic() - 1)
            finally:
                os.close(descriptor)

    @unittest.skipUnless(sys.platform == "darwin", "requires macOS ACLs")
    def test_real_acl_snapshot_and_copy_roundtrip_cannot_crash(self):
        import pwd
        with tempfile.TemporaryDirectory() as directory:
            source, destination = Path(directory) / "source", Path(directory) / "destination"
            source.write_bytes(b"source")
            destination.write_bytes(b"destination")
            user = pwd.getpwuid(os.getuid()).pw_name
            subprocess.run(
                ["/bin/chmod", "+a", f"{user} allow read", str(source)], check=True,
            )
            script = f"""
import os
from pathlib import Path
from evergreen import host_metadata
source, destination = Path({str(source)!r}), Path({str(destination)!r})
s, d = os.open(source, os.O_RDONLY), os.open(destination, os.O_RDWR)
try:
    metadata = os.fstat(s)
    before = host_metadata.digest_fd(s)
    host_metadata.clone(s, d, metadata, metadata.st_atime_ns, metadata.st_mtime_ns)
    after = host_metadata.digest_fd(d)
    assert before == after
finally:
    os.close(s); os.close(d)
"""
            result = subprocess.run(
                [sys.executable, "-c", script], cwd=ROOT,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
