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


def resolve_managed_root(home, root):
    try:
        home = normalized_lexical_path(home)
        chain = [root, home]
        home_metadata = home.lstat()
        if home_metadata.st_uid != os.getuid() or home_metadata.st_mode & 0o002:
            raise OSError(f"managed host home is unsafe: {home}")
        root_metadata = root.lstat()
        if root_metadata.st_uid != os.getuid():
            raise OSError("host root link is not user-owned")
        raw_target = Path(os.readlink(root))
        target = raw_target if raw_target.is_absolute() else root.parent / raw_target
        try:
            pending = list(target.relative_to(home).parts)
        except ValueError as error:
            raise OSError("managed host root target leaves home") from error
        current = home
        seen_links = {(root_metadata.st_dev, root_metadata.st_ino)}
        hops = 0
        while pending:
            part = pending.pop(0)
            if part in ("", "."):
                continue
            if part == "..":
                raise OSError("managed host root chain leaves home")
            candidate = current / part
            metadata = candidate.lstat()
            chain.append(candidate)
            if kind(candidate) == "symlink":
                hops += 1
                identity = metadata.st_dev, metadata.st_ino
                if hops > 40 or identity in seen_links:
                    raise OSError("managed host root chain has a cycle")
                if metadata.st_uid != os.getuid():
                    raise OSError(f"managed host root link is not user-owned: {candidate}")
                seen_links.add(identity)
                link_target = Path(os.readlink(candidate))
                if link_target.is_absolute():
                    try:
                        pending = list(link_target.relative_to(home).parts) + pending
                    except ValueError as error:
                        raise OSError("managed host root link leaves home") from error
                    current = home
                else:
                    pending = list(link_target.parts) + pending
                    current = candidate.parent
                continue
            if kind(candidate) != "directory":
                raise OSError(f"managed host root chain is not a directory: {candidate}")
            if metadata.st_uid != os.getuid() or metadata.st_mode & 0o002:
                raise OSError(f"managed host root chain is unsafe: {candidate}")
            current = candidate
        resolved = current.resolve(strict=True)
        resolved.relative_to(home.resolve())
        return resolved, tuple(dict.fromkeys(chain)), None
    except (OSError, RuntimeError, ValueError) as error:
        return root, (), f"unsafe managed host root: {error}"


def capture_authorization(selected):
    captured = {}
    for status in selected:
        paths = (status.root,) if not status.managed_chain else (
            *status.managed_chain, status.resolved_root, status.ownership,
        )
        for path in paths:
            if path not in captured:
                if path == status.ownership:
                    parent = os.open(
                        path.parent,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                        getattr(os, "O_NOFOLLOW", 0),
                    )
                    try:
                        captured[path] = snapshot_at(path, parent)
                    finally:
                        os.close(parent)
                    if captured[path].kind == "regular" and captured[path].nlink > 2:
                        raise OSError(f"refusing hard-linked authorization path: {path}")
                else:
                    captured[path] = snapshot(path, allow_directory=True)
        if status.managed_chain:
            verify_managed_root_binding(status, captured)
    return captured


def capture_preflight(selected):
    captured = {}
    for status in selected:
        for path, allow_directory in (
            (status.instructions, False), (status.skill, False),
            (status.ownership, False), (status.skill.parent, True),
            (status.root, True), (status.resolved_root, True),
        ):
            if path not in captured:
                captured[path] = snapshot(path, allow_directory=allow_directory)
        if status.root != status.resolved_root:
            verify_managed_root_binding(status)
    return captured


def verify_managed_root_binding(status, captured=None):
    if status.root == status.resolved_root:
        return
    try:
        resolved = status.root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError) as error:
        raise OSError(f"managed host root changed: {status.root}: {error}") from error
    if resolved != status.resolved_root:
        raise OSError(f"managed host root changed: {status.root}")
    for path in status.managed_chain:
        current = snapshot(path, allow_directory=True)
        expected = captured.get(path) if captured is not None else None
        if expected is not None and current != expected:
            raise OSError(f"managed host root chain changed: {path}")
        if current.uid != os.getuid():
            raise OSError(f"managed host root chain is not user-owned: {path}")
        if current.kind == "directory" and current.mode & 0o002:
            raise OSError(f"managed host root chain is unsafe: {path}")
        if current.kind not in ("directory", "symlink"):
            raise OSError(f"managed host root chain is unsafe: {path}")


def verify_locked_authorization(selected, captured, root_descriptors):
    ownership_paths = {status.ownership for status in selected if status.managed_chain}
    verify_preflight({
        path: item for path, item in captured.items() if path not in ownership_paths
    })
    for status in selected:
        root = captured[status.resolved_root]
        metadata = os.fstat(root_descriptors[status.resolved_root])
        if not root.matches_stat(metadata):
            raise OSError(f"managed host root changed before locking: {status.root}")
        if status.managed_chain:
            verify_managed_root_binding(status, captured)
            ownership = snapshot_at(
                status.ownership, root_descriptors[status.resolved_root]
            )
            if ownership != captured[status.ownership]:
                raise OSError(f"managed host ownership changed before locking: {status.root}")


def verify_pinned_roots(selected, captured, root_descriptors):
    for status in selected:
        expected = captured[status.resolved_root]
        descriptor_metadata = os.fstat(root_descriptors[status.resolved_root])
        live = snapshot(status.resolved_root, allow_directory=True)
        identity = lambda item: (
            item.kind, item.dev, item.ino, item.mode, item.uid, item.gid,
        )
        descriptor_identity = (
            kind_from_mode(descriptor_metadata.st_mode), descriptor_metadata.st_dev,
            descriptor_metadata.st_ino, stat.S_IMODE(descriptor_metadata.st_mode),
            descriptor_metadata.st_uid, descriptor_metadata.st_gid,
        )
        if identity(live) != identity(expected) or descriptor_identity != identity(expected):
            raise OSError(f"managed host destination changed: {status.root}")
        if not status.managed_chain:
            continue
        if status.root.resolve(strict=True) != status.resolved_root:
            raise OSError(f"managed host root changed: {status.root}")
        for path in status.managed_chain:
            chain_expected = captured[path]
            chain_live = snapshot(path, allow_directory=True)
            if chain_expected.kind == "directory":
                if identity(chain_live) != identity(chain_expected):
                    raise OSError(f"managed host root chain changed: {path}")
            elif chain_live != chain_expected:
                raise OSError(f"managed host root chain changed: {path}")


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
        if (
            after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
            after.st_uid, after.st_gid, after.st_mtime_ns,
        ) != (
            metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink,
            metadata.st_uid, metadata.st_gid, metadata.st_mtime_ns,
        ):
            raise OSError(f"transaction symlink changed: {path}")
        return PathSnapshot(
            path, item_kind, target=target,
            # Reading either name of a hard-linked symlink may advance the
            # shared inode's atime on Linux. It is observation state, not an
            # integrity signal; target, inode, ownership, mode, nlink, and
            # mtime remain verified above.
            **{**common, "atime_ns": None, "mtime_ns": after.st_mtime_ns},
        )
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
