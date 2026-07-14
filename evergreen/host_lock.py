"""Portable boundary and descriptor-safe locking for host operations."""

import os
import stat
import sys

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows smoke coverage.
    fcntl = None

from .host_snapshot import open_directory, snapshot, verify_locked_authorization


def runtime_error(native_metadata_copy_available):
    if sys.version_info < (3, 11):
        return "error: host management requires Python 3.11 or newer"
    if fcntl is None:
        return "error: host management requires POSIX file locking"
    if sys.platform == "darwin" and not native_metadata_copy_available():
        return "error: host management requires macOS Python metadata-copy support"
    return None


def acquire(selected, authorization):
    descriptors = []
    roots = {}
    try:
        homes = sorted({status.root.parent for status in selected}, key=str)
        for home in homes:
            home_snapshot = snapshot(home, allow_directory=True)
            if home_snapshot.uid != os.getuid() or home_snapshot.mode & 0o022:
                raise OSError(f"unsafe host transaction home: {home}")
            descriptor = open_directory(home_snapshot)
            _lock_directory(descriptor, home, descriptors)
            roots[home] = descriptor
        for status in sorted(selected, key=lambda item: str(item.resolved_root)):
            if status.resolved_root in roots:
                continue
            descriptor = open_directory(
                snapshot(status.resolved_root, allow_directory=True)
            )
            _lock_directory(descriptor, status.root, descriptors)
            roots[status.resolved_root] = descriptor
            try:
                legacy = os.open(
                    ".evergreen-host.lock",
                    os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
            except FileNotFoundError:
                continue
            _lock_legacy(legacy, status.root, descriptors)
        verify_locked_authorization(selected, authorization, roots)
    except OSError as error:
        cleanup_errors = release(descriptors)
        cleanup = "" if not cleanup_errors else "; cleanup failed: " + "; ".join(cleanup_errors)
        return [], {}, f"error: another host operation is active or locking failed: {error}{cleanup}"
    return descriptors, roots, None


def _lock_directory(descriptor, root, descriptors):
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError(f"unsafe host lock directory: {root}")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(descriptor)
        raise
    descriptors.append(descriptor)


def _lock_legacy(descriptor, root, descriptors):
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
            metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise OSError(f"unsafe legacy host lock file: {root}")
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
