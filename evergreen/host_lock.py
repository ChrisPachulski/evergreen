"""Portable boundary and descriptor-safe locking for host operations."""

import os
import stat
import sys

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows smoke coverage.
    fcntl = None

from .host_snapshot import open_directory, snapshot


def runtime_error(native_metadata_copy_available):
    if sys.version_info < (3, 11):
        return "error: host management requires Python 3.11 or newer"
    if fcntl is None:
        return "error: host management requires POSIX file locking"
    if sys.platform == "darwin" and not native_metadata_copy_available():
        return "error: host management requires macOS Python metadata-copy support"
    return None


def acquire(selected):
    descriptors = []
    missing = []
    try:
        for status in sorted(selected, key=lambda item: str(item.root)):
            root = open_directory(snapshot(status.root, allow_directory=True))
            try:
                flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                try:
                    descriptor = os.open(".evergreen-host.lock", flags, dir_fd=root)
                except FileNotFoundError:
                    missing.append((status, root))
                    root = None
                    continue
            finally:
                if root is not None:
                    os.close(root)
            _lock_descriptor(descriptor, status.root, descriptors)
        while missing:
            status, root = missing.pop(0)
            try:
                flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                try:
                    descriptor = os.open(".evergreen-host.lock", flags, 0o600, dir_fd=root)
                except FileExistsError:
                    descriptor = os.open(
                        ".evergreen-host.lock", os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=root,
                    )
            finally:
                os.close(root)
            _lock_descriptor(descriptor, status.root, descriptors)
    except OSError as error:
        for _, root in missing:
            try:
                os.close(root)
            except OSError:
                pass
        cleanup_errors = release(descriptors)
        cleanup = "" if not cleanup_errors else "; cleanup failed: " + "; ".join(cleanup_errors)
        return [], f"error: another host operation is active or locking failed: {error}{cleanup}"
    return descriptors, None


def _lock_descriptor(descriptor, root, descriptors):
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise OSError(f"unsafe host lock file: {root}")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise
    descriptors.append(descriptor)


def release(descriptors):
    errors = []
    for descriptor in reversed(descriptors):
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError as error:
            errors.append(f"unlock fd {descriptor}: {error}")
        finally:
            try:
                os.close(descriptor)
            except OSError as error:
                errors.append(f"close fd {descriptor}: {error}")
    return errors
