"""Bounded, descriptor-relative snapshots for host transactions."""

import os
from pathlib import Path
import stat
import time

from .host_metadata import digest_fd as metadata_digest_fd
from .host_types import PathSnapshot

OWNERSHIP_FILE = ".evergreen-owned.json"
MAX_STATE_BYTES = 4096
MAX_INSTRUCTION_BYTES = 1024 * 1024
READ_ELAPSED_LIMIT_SECONDS = 3


def capture_preflight(selected):
    captured = {}
    for status in selected:
        for path, allow_directory in (
            (status.instructions, False), (status.skill, False),
            (status.ownership, False), (status.skill.parent, True),
            (status.root, True),
        ):
            if path not in captured:
                captured[path] = snapshot(path, allow_directory=allow_directory)
    return captured


def verify_preflight(captured):
    for item in captured.values():
        verify_snapshot(item)


def read_regular_bounded(path, limit, label):
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise OSError(f"refusing {label}: nonblocking reads unavailable")
    deadline = time.monotonic() + READ_ELAPSED_LIMIT_SECONDS
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OSError(f"refusing unsafe {label}: {path}")
    if before.st_size > limit:
        raise ValueError(f"{label} exceeds byte limit (maximum {limit})")
    descriptor = os.open(path, os.O_RDONLY | nonblocking | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            raise OSError(f"refusing changed {label}: {path}")
        chunks, remaining = [], limit + 1
        while remaining:
            if time.monotonic() > deadline:
                raise TimeoutError(f"{label} read exceeded elapsed-time limit")
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        after = path.lstat()
        def identity(item):
            return (
                item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_size,
                item.st_mtime_ns, item.st_ctime_ns,
            )
        if len(data) > limit:
            raise ValueError(f"{label} exceeds byte limit (maximum {limit})")
        if identity(after) != identity(opened) or identity(before) != identity(opened):
            raise OSError(f"refusing changed {label}: {path}")
        return data
    finally:
        os.close(descriptor)


def kind(path):
    try:
        return kind_from_mode(Path(path).lstat().st_mode)
    except FileNotFoundError:
        return "absent"


def kind_from_mode(mode):
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "regular"
    return "other"


def snapshot(path, allow_directory=False):
    path = Path(path)
    if kind(path) == "absent":
        return PathSnapshot(path, "absent")
    parent = os.open(
        path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        result = snapshot_at(path, parent)
    finally:
        os.close(parent)
    if result.kind == "regular" and result.nlink != 1:
        raise OSError(f"refusing hard-linked transaction path: {path}")
    if result.kind == "directory" and not allow_directory:
        raise OSError(f"refusing unsafe transaction path (directory): {path}")
    return result


def open_directory(item):
    if item.kind != "directory":
        raise OSError(f"transaction parent is {item.kind}: {item.path}")
    descriptor = os.open(
        item.path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
        getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISDIR(metadata.st_mode) or
                (metadata.st_dev, metadata.st_ino) != (item.dev, item.ino) or
                stat.S_IMODE(metadata.st_mode) != item.mode):
            raise OSError(f"transaction directory changed: {item.path}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def snapshot_at(path, parent_fd):
    deadline = time.monotonic() + READ_ELAPSED_LIMIT_SECONDS
    try:
        metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return PathSnapshot(path, "absent")
    item_kind = kind_from_mode(metadata.st_mode)
    common = dict(
        mode=stat.S_IMODE(metadata.st_mode), dev=metadata.st_dev, ino=metadata.st_ino,
        nlink=metadata.st_nlink, uid=metadata.st_uid, gid=metadata.st_gid,
        atime_ns=metadata.st_atime_ns, mtime_ns=metadata.st_mtime_ns,
    )
    if item_kind == "regular":
        limit = MAX_STATE_BYTES if (
            path.name == OWNERSHIP_FILE or "evergreen-journal-" in path.name
        ) else MAX_INSTRUCTION_BYTES
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise OSError(f"transaction path changed: {path}")
            chunks, remaining = [], limit + 1
            while remaining:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"transaction snapshot exceeded elapsed-time limit: {path}")
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > limit:
                raise ValueError(f"transaction snapshot exceeds byte limit: {path}")
            digest = metadata_digest_fd(descriptor, deadline=deadline)
            os.utime(descriptor, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            after_open = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        def identity(item):
            return (
                item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
                item.st_size, item.st_mtime_ns,
            )
        if identity(metadata) != identity(after_open) or identity(metadata) != identity(after_path):
            raise OSError(f"transaction path changed: {path}")
        return PathSnapshot(path, item_kind, data=data, metadata_digest=digest, **common)
    if item_kind == "symlink":
        target = os.readlink(path.name, dir_fd=parent_fd)
        after = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (after.st_dev, after.st_ino, after.st_mode, after.st_nlink) != (
            metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
        ):
            raise OSError(f"transaction symlink changed: {path}")
        return PathSnapshot(path, item_kind, target=target, **common)
    if item_kind == "directory":
        return PathSnapshot(path, item_kind, **common)
    raise OSError(f"refusing unsafe transaction path ({item_kind}): {path}")


def verify_snapshot(expected):
    if snapshot(expected.path, allow_directory=expected.kind == "directory") != expected:
        raise OSError(f"transaction path changed after planning: {expected.path}")


def verify_snapshot_at(expected, parent_fd):
    if snapshot_at(expected.path, parent_fd) != expected:
        raise OSError(f"transaction path changed after planning: {expected.path}")


def verify_open_directory_path(path, descriptor):
    expected, actual = os.fstat(descriptor), path.lstat()
    def identity(item):
        return item.st_dev, item.st_ino, item.st_mode, item.st_nlink
    if identity(actual) != identity(expected):
        raise OSError(f"transaction directory changed during mutation: {path}")


def normalized_lexical_path(path):
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def normalized_snapshot_target(item):
    if item.kind != "symlink" or item.target is None:
        return None
    target = Path(item.target)
    return normalized_lexical_path(target if target.is_absolute() else item.path.parent / target)
