"""Reversible Claude and Codex host integration."""
from dataclasses import replace
import os
from pathlib import Path
import stat
import sys
import time
import uuid
import fcntl
OWNERSHIP_FILE = ".evergreen-owned.json"
MAX_STATE_BYTES = 4096
MAX_INSTRUCTION_BYTES = 1024 * 1024
READ_ELAPSED_LIMIT_SECONDS = 3
from .host_types import (JournalPhase, JournalRecord, MutationKind, OperationResult,
    PathSnapshot, RollbackEntry,
)
from .host_metadata import digest_fd as _metadata_digest_fd
from .host_metadata import digest_path as _metadata_digest_path
from .host_metadata import clone as _clone_regular_metadata
from .host_metadata import native_copy_available as _native_metadata_copy_available
class TransactionEngine:
    def __init__(self, selected, locks):
        self.selected, self._locks = tuple(selected), locks
    @classmethod
    def acquire(cls, selected):
        locks, error = _lock_hosts(selected); return (None, error) if error else (cls(selected, locks), None)
    def recover(self): return _recover_transactions(self.selected)
    def apply(self, plans, dry_run, captured): return _apply(plans, dry_run, captured)
    def close(self):
        _unlock_hosts(self._locks); self._locks = []
def _host_runtime_error():
    if sys.version_info < (3, 11):
        return "error: host management requires Python 3.11 or newer"
    return (
        "error: host management requires macOS Python metadata-copy support"
        if sys.platform == "darwin" and not _native_metadata_copy_available() else None)
def _lock_hosts(selected):
    descriptors = []
    missing = []
    try:
        for status in sorted(selected, key=lambda item: str(item.root)):
            snapshot = _snapshot(status.root, allow_directory=True)
            root_fd = _open_directory(snapshot)
            try:
                flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                try:
                    descriptor = os.open(".evergreen-host.lock", flags, dir_fd=root_fd)
                except FileNotFoundError:
                    missing.append((status, root_fd))
                    root_fd = None
                    continue
            finally:
                if root_fd is not None:
                    os.close(root_fd)
            try:
                _verify_lock_descriptor(descriptor, status.root)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
        while missing:
            status, root_fd = missing.pop(0)
            try:
                flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                try:
                    descriptor = os.open(
                        ".evergreen-host.lock", flags, 0o600, dir_fd=root_fd,
                    )
                except FileExistsError:
                    descriptor = os.open(
                        ".evergreen-host.lock",
                        os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=root_fd,
                    )
            finally:
                os.close(root_fd)
            try:
                _verify_lock_descriptor(descriptor, status.root)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BaseException:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
    except OSError as error:
        for _, root_fd in missing:
            try:
                os.close(root_fd)
            except OSError:
                pass
        _unlock_hosts(descriptors)
        return [], f"error: another host operation is active or locking failed: {error}"
    return descriptors, None
def _verify_lock_descriptor(descriptor, root):
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or
        metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise OSError(f"unsafe host lock file: {root}")
def _unlock_hosts(descriptors):
    for descriptor in reversed(descriptors):
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
def _recover_transactions(selected):
    errors = []
    for status in selected:
        for target in (
            status.instructions, status.ownership, status.skill, status.skill.parent,
        ):
            if _kind(target.parent) != "directory":
                continue
            parent = _snapshot(target.parent, allow_directory=True)
            descriptor = _open_directory(parent)
            try:
                error = _recover_target_artifacts(descriptor, target)
                if error:
                    errors.append(f"{status.name}: {error}")
            finally:
                os.close(descriptor)
    return errors
def _recover_target_artifacts(parent_fd, target):
    deadline = time.monotonic() + READ_ELAPSED_LIMIT_SECONDS
    groups = {}
    try:
        with os.scandir(parent_fd) as entries:
            for index, entry in enumerate(entries, start=1):
                if index > 128 or time.monotonic() > deadline:
                    return f"artifact scan limit exceeded in {target.parent}; inspect manually"
                parsed = _artifact_name(target.name, entry.name)
                if parsed:
                    kind, transaction_id = parsed
                    groups.setdefault(transaction_id, {})[kind] = entry.name
    except OSError as error:
        return f"artifact scan failed in {target.parent}: {error}"
    paths = sorted(
        str(target.parent / name)
        for artifacts in groups.values() for name in artifacts.values()
    )
    if not groups:
        return None
    if len(groups) != 1 or len(paths) > 16:
        return _manual_artifact_error(paths[:16])
    transaction_id, artifacts = next(iter(groups.items()))
    journal_name = artifacts.get("journal")
    if journal_name is None:
        return _manual_artifact_error(paths)
    try:
        snapshot = _snapshot_at(target.with_name(journal_name), parent_fd)
        record = JournalRecord.parse(snapshot.data)
        if not _journal_names_match(record, target.name, transaction_id, artifacts):
            return _manual_artifact_error(paths)
        _recover_record(parent_fd, target, record, artifacts)
        return None
    except (OSError, TypeError, ValueError, KeyError):
        return _manual_artifact_error(paths)
def _journal_names_match(record, target, transaction_id, artifacts):
    if record.target != target or record.transaction_id != transaction_id:
        return False
    expected = {"journal": record.journal}
    if record.temporary is not None:
        expected["temporary"] = record.temporary
    if record.backup is not None:
        expected["backup"] = record.backup
    return (
        record.journal == f".{target}.evergreen-journal-{transaction_id}" and
        (record.temporary is None or
         record.temporary == f".{target}.evergreen-{transaction_id}") and
        (record.backup is None or
         record.backup == f".{target}.evergreen-backup-{transaction_id}") and
        set(artifacts).issubset(expected) and
        all(expected[kind] == name for kind, name in artifacts.items())
    )
def _recover_record(parent_fd, target, record, artifacts):
    live = _snapshot_at(target, parent_fd)
    staged = _artifact_snapshot(parent_fd, target, artifacts.get("temporary"))
    backup = _artifact_snapshot(parent_fd, target, artifacts.get("backup"))
    creates = {
        MutationKind.CREATE_REGULAR, MutationKind.CREATE_SYMLINK,
        MutationKind.CREATE_DIRECTORY,
    }
    replaces = {MutationKind.REPLACE_REGULAR, MutationKind.REPLACE_SYMLINK}
    if record.mutation in creates | replaces:
        if staged.kind != "absent":
            if not _journal_snapshot_matches(staged, record.after):
                raise ValueError("staged postimage changed")
            if record.mutation in creates:
                if live.kind != "absent" or backup.kind != "absent":
                    raise ValueError("create preimage changed")
            else:
                links = 2 if backup.kind != "absent" else 1
                if not _journal_snapshot_matches(live, record.before, nlink=links):
                    raise ValueError("replace preimage changed")
                if backup.kind != "absent" and (
                    (live.dev, live.ino) != (backup.dev, backup.ino) or
                    backup.nlink != 2
                ):
                    raise ValueError("replace backup changed")
            _remove_kind(parent_fd, record.temporary, staged.kind)
            if backup.kind != "absent":
                _remove_kind(parent_fd, record.backup, backup.kind)
        else:
            after_matches = _journal_snapshot_matches(live, record.after)
            if live.kind == "directory":
                after_matches = all(
                    live.journal_identity().get(field) == record.after.get(field)
                    for field in ("kind", "dev", "ino", "mode", "uid", "gid")
                )
            if not after_matches:
                raise ValueError("published postimage changed")
            if record.mutation in creates:
                _remove_kind(parent_fd, target.name, live.kind)
            else:
                if not _journal_snapshot_matches(backup, record.before):
                    raise ValueError("replace backup changed")
                os.replace(record.backup, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    else:
        if backup.kind == "absent":
            if not _journal_snapshot_matches(live, record.before):
                raise ValueError("delete preimage changed")
        else:
            if live.kind != "absent" or not _journal_snapshot_matches(
                backup, record.before,
            ):
                raise ValueError("delete backup changed")
            os.replace(record.backup, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.unlink(record.journal, dir_fd=parent_fd)
    os.fsync(parent_fd)
def _artifact_snapshot(parent_fd, target, name):
    if name is None:
        return PathSnapshot(target, "absent")
    return _snapshot_at(target.with_name(name), parent_fd)
def _artifact_name(target_name, name):
    prefixes = (
        ("backup", f".{target_name}.evergreen-backup-"),
        ("journal", f".{target_name}.evergreen-journal-"),
        ("temporary", f".{target_name}.evergreen-"),
    )
    for kind, prefix in prefixes:
        transaction_id = name.removeprefix(prefix)
        if transaction_id != name and len(transaction_id) == 32 and all(
            char in "0123456789abcdef" for char in transaction_id
        ):
            return kind, transaction_id
    return None
def _manual_artifact_error(paths):
    return ("transaction artifacts require manual recovery: " + ", ".join(paths) +
            "; inspect the journal and restore the named backup before removing artifacts")
def _remove_kind(parent_fd, name, kind):
    (os.rmdir if kind == "directory" else os.unlink)(name, dir_fd=parent_fd)
def _journal_snapshot_matches(snapshot, expected, *, nlink=None):
    if not isinstance(expected, dict) or (nlink is not None and snapshot.nlink != nlink):
        return False
    actual = snapshot.journal_identity()
    expected = dict(expected)
    if nlink is not None:
        expected["nlink"] = nlink
    return actual == expected
def _apply(plans, dry_run, captured):
    messages = []
    for status, (action, detail, path, value) in plans:
        prefix = "would " if dry_run and action not in ("unchanged", "unowned") else ""
        messages.append(f"{status.name}: {prefix}{detail}")
    mutations = [plan for _status, plan in plans if plan[0] not in ("unchanged", "unowned")]
    try:
        _verify_preflight(captured)
    except Exception as error:
        return OperationResult(False, tuple(messages + [f"error: transaction preflight failed: {error}"]))
    if dry_run:
        return OperationResult(True, tuple(messages))
    messages.append(
        "note: host operations require exclusive access; conflict checks are not "
        "portable compare-and-swap"
    )
    rollback_entries = []
    conflicts = []
    try:
        for action, _detail, path, value in mutations:
            parent_fd = _prepare_parent(
                path.parent, captured, rollback_entries, conflicts
            )
            try:
                _verify_snapshot_at(captured[path], parent_fd)
                try:
                    postimage = _perform_action(
                        action, path, value, parent_fd=parent_fd,
                        expected=captured[path], parent_snapshot=captured[path.parent],
                        rollback_entries=rollback_entries, conflicts=conflicts,
                    )
                except Exception:
                    if not _entry_for_path(rollback_entries, path):
                        try:
                            changed = _snapshot_at(path, parent_fd) != captured[path]
                        except Exception:
                            changed = True
                        if changed:
                            conflicts.append(f"{path}: preserved concurrent state")
                    raise
                current = _snapshot_at(path, parent_fd)
                if current != postimage:
                    conflicts.append(f"{path}: preserved concurrent state")
                    raise OSError(f"transaction postimage mismatch: {path}")
                if not _entry_for_path(rollback_entries, path):
                    rollback_entries.append(RollbackEntry(
                        captured[path], postimage, captured[path.parent]
                    ))
                _verify_open_directory_path(path.parent, parent_fd)
            finally:
                os.close(parent_fd)
    except Exception as error:
        rollback_errors = []
        for entry in reversed(rollback_entries):
            try:
                _restore_entry(entry)
            except Exception as rollback_error:
                rollback_errors.append(f"{entry.before.path}: {rollback_error}")
        recovery = conflicts + rollback_errors
        if recovery:
            return OperationResult(False, tuple(messages + [
                f"error: apply failed: {error}; verified rollback incomplete; "
                "manual recovery required: "
                + "; ".join(recovery)
            ]))
        return OperationResult(False, tuple(messages + [
            f"error: apply failed: {error}; ordinary recovery completed under the "
            "exclusive-access requirement"
        ]))
    cleanup_errors = []
    for entry in reversed(rollback_entries):
        if entry.backup is None and entry.journal is None:
            continue
        try:
            _commit_entry(entry)
        except Exception as cleanup_error:
            cleanup_errors.append(f"{entry.before.path}: {cleanup_error}")
    if cleanup_errors:
        return OperationResult(False, tuple(messages + [
            "error: changes applied but transaction backup cleanup failed; "
            "manual recovery required: " + "; ".join(cleanup_errors)
        ]))
    return OperationResult(True, tuple(messages))
def _entry_for_path(entries, path):
    return next((entry for entry in reversed(entries) if entry.before.path == path), None)
def _capture_preflight(selected):
    captured = {}
    for status in selected:
        for path, allow_directory in (
            (status.instructions, False),
            (status.skill, False),
            (status.ownership, False),
            (status.skill.parent, True),
            (status.root, True),
        ):
            if path not in captured:
                captured[path] = _snapshot(path, allow_directory=allow_directory)
    return captured
def _verify_preflight(captured):
    for snapshot in captured.values(): _verify_snapshot(snapshot)
def _read_regular_bounded(path, limit, label):
    nonblocking = getattr(os, "O_NONBLOCK", None)
    if nonblocking is None:
        raise OSError(f"refusing {label}: nonblocking reads unavailable")
    deadline = time.monotonic() + READ_ELAPSED_LIMIT_SECONDS
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise OSError(f"refusing unsafe {label}: {path}")
    if before.st_size > limit:
        raise ValueError(f"{label} exceeds byte limit (maximum {limit})")
    flags = os.O_RDONLY | nonblocking | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            raise OSError(f"refusing changed {label}: {path}")
        chunks = []
        remaining = limit + 1
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
        identity = lambda item: (
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
def _verify_snapshot(expected):
    if _snapshot(expected.path, allow_directory=expected.kind == "directory") != expected:
        raise OSError(f"transaction path changed after planning: {expected.path}")
def _snapshot(path, allow_directory=False):
    path = Path(path)
    if _kind(path) == "absent":
        return PathSnapshot(path, "absent")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_fd = os.open(path.parent, flags)
    try:
        snapshot = _snapshot_at(path, parent_fd)
    finally:
        os.close(parent_fd)
    if snapshot.kind == "regular" and snapshot.nlink != 1:
        raise OSError(f"refusing hard-linked transaction path: {path}")
    if snapshot.kind == "directory" and not allow_directory:
        raise OSError(f"refusing unsafe transaction path (directory): {path}")
    return snapshot
def _open_directory(snapshot):
    if snapshot.kind != "directory":
        raise OSError(f"transaction parent is {snapshot.kind}: {snapshot.path}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(snapshot.path, flags)
    try:
        metadata = os.fstat(descriptor)
        if (not stat.S_ISDIR(metadata.st_mode) or
                (metadata.st_dev, metadata.st_ino) != (snapshot.dev, snapshot.ino) or
                stat.S_IMODE(metadata.st_mode) != snapshot.mode):
            raise OSError(f"transaction directory changed: {snapshot.path}")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor
def _prepare_parent(path, captured, rollback_entries, conflicts):
    snapshot = captured[path]
    if snapshot.kind == "directory":
        return _open_directory(snapshot)
    if snapshot.kind != "absent":
        raise OSError(f"transaction parent is {snapshot.kind}: {path}")
    grandparent = captured[path.parent]
    descriptor = _open_directory(grandparent)
    created = None
    try:
        _verify_snapshot_at(snapshot, descriptor)
        created = _publish_path(
            descriptor, path, MutationKind.CREATE_DIRECTORY, snapshot, grandparent,
            rollback_entries, conflicts, mode=0o755,
        )
    except Exception as error:
        if created is None:
            try:
                current = _snapshot_at(path, descriptor)
            except Exception:
                current = None
            if current != snapshot:
                conflicts.append(f"{path}: preserved concurrent state")
        raise error
    finally:
        os.close(descriptor)
    captured[path] = created
    return _open_directory(created)
def _kind_from_mode(mode):
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "regular"
    return "other"
def _kind(path):
    try:
        return _kind_from_mode(Path(path).lstat().st_mode)
    except FileNotFoundError:
        return "absent"
def _normalized_lexical_path(path):
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))
def _normalized_snapshot_target(snapshot):
    if snapshot.kind != "symlink" or snapshot.target is None:
        return None
    target = Path(snapshot.target)
    return _normalized_lexical_path(
        target if target.is_absolute() else snapshot.path.parent / target
    )
def _snapshot_at(path, parent_fd):
    try:
        metadata = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return PathSnapshot(path, "absent")
    kind = _kind_from_mode(metadata.st_mode)
    mode = stat.S_IMODE(metadata.st_mode)
    common = {
        "mode": mode, "dev": metadata.st_dev, "ino": metadata.st_ino,
        "nlink": metadata.st_nlink,
        "uid": metadata.st_uid, "gid": metadata.st_gid,
        "atime_ns": metadata.st_atime_ns, "mtime_ns": metadata.st_mtime_ns,
    }
    if kind == "regular":
        limit = (
            MAX_STATE_BYTES
            if path.name == OWNERSHIP_FILE or "evergreen-journal-" in path.name
            else MAX_INSTRUCTION_BYTES
        )
        flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path.name, flags, dir_fd=parent_fd)
        try:
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise OSError(f"transaction path changed: {path}")
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > limit:
                raise ValueError(f"transaction snapshot exceeds byte limit: {path}")
            metadata_digest = _metadata_digest_fd(descriptor)
            os.utime(descriptor, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
            after_open = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_size,
            item.st_mtime_ns,
        )
        if identity(metadata) != identity(after_open) or identity(metadata) != identity(after_path):
            raise OSError(f"transaction path changed: {path}")
        return PathSnapshot(
            path, kind, data=data, metadata_digest=metadata_digest, **common,
        )
    if kind == "symlink":
        target = os.readlink(path.name, dir_fd=parent_fd)
        after = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (after.st_dev, after.st_ino, after.st_mode, after.st_nlink) != (
                metadata.st_dev, metadata.st_ino, metadata.st_mode, metadata.st_nlink):
            raise OSError(f"transaction symlink changed: {path}")
        return PathSnapshot(path, kind, target=target, **common)
    if kind == "directory":
        return PathSnapshot(path, kind, **common)
    raise OSError(f"refusing unsafe transaction path ({kind}): {path}")
def _verify_snapshot_at(expected, parent_fd):
    actual = _snapshot_at(expected.path, parent_fd)
    if actual != expected:
        raise OSError(f"transaction path changed after planning: {expected.path}")
def _verify_open_directory_path(path, descriptor):
    expected = os.fstat(descriptor)
    actual = path.lstat()
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink,
    )
    if identity(actual) != identity(expected):
        raise OSError(f"transaction directory changed during mutation: {path}")
def _matches_postimage(action, value, snapshot, expected):
    if action == "write":
        mode = expected.mode if expected.kind == "regular" else (
            0o600 if expected.path.name == OWNERSHIP_FILE else 0o644
        )
        return (snapshot.kind == "regular" and snapshot.data == value and
                snapshot.mode == mode and snapshot.nlink == 1)
    if action == "link":
        return (snapshot.kind == "symlink" and
                snapshot.nlink == 1 and
                _normalized_snapshot_target(snapshot) == _normalized_lexical_path(value))
    if action == "delete":
        return snapshot.kind == "absent"
    return False
def _perform_action(
    action, path, value, *, parent_fd, expected, parent_snapshot,
    rollback_entries, conflicts,
):
    _verify_snapshot_at(expected, parent_fd)
    if action == "write":
        kind = (
            MutationKind.CREATE_REGULAR if expected.kind == "absent"
            else MutationKind.REPLACE_REGULAR
        )
        mode = 0o600 if path.name == OWNERSHIP_FILE else 0o644
        return _publish_path(
            parent_fd, path, kind, expected, parent_snapshot,
            rollback_entries, conflicts, data=value, mode=mode,
        )
    elif action == "link":
        kind = (
            MutationKind.CREATE_SYMLINK if expected.kind == "absent"
            else MutationKind.REPLACE_SYMLINK
        )
        return _publish_path(
            parent_fd, path, kind, expected, parent_snapshot,
            rollback_entries, conflicts, target=value,
        )
    elif action == "delete":
        if expected.kind == "absent":
            return expected
        return _publish_delete(
            parent_fd, path, expected, parent_snapshot,
            rollback_entries, conflicts,
        )
    postimage = _snapshot_at(path, parent_fd)
    if not _matches_postimage(action, value, postimage, expected):
        raise OSError(f"transaction postimage mismatch: {path}")
    return postimage
def _publish_path(
    parent_fd, path, kind, before, parent_snapshot, rollback_entries, conflicts,
    *, data=None, target=None, mode=None,
):
    transaction_id = uuid.uuid4().hex
    temporary = f".{path.name}.evergreen-{transaction_id}"
    journal_name = f".{path.name}.evergreen-journal-{transaction_id}"
    backup = (
        f".{path.name}.evergreen-backup-{transaction_id}"
        if before.kind != "absent" else None
    )
    published = False
    journal_created = False
    backup_created = False
    try:
        if kind in {MutationKind.CREATE_REGULAR, MutationKind.REPLACE_REGULAR}:
            descriptor = os.open(
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode,
                dir_fd=parent_fd,
            )
            try:
                _replace_descriptor_bytes(descriptor, data, path)
                if before.kind == "regular":
                    source = os.open(
                        path.name, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
                        getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd,
                    )
                    try:
                        source_metadata = os.fstat(source)
                        _clone_regular_metadata(
                            source, descriptor, source_metadata,
                            before.atime_ns, before.mtime_ns,
                        )
                        os.utime(source, ns=(before.atime_ns, before.mtime_ns))
                        os.fsync(source)
                    finally:
                        os.close(source)
                else:
                    os.fchmod(descriptor, mode)
            finally:
                os.close(descriptor)
        elif kind in {MutationKind.CREATE_SYMLINK, MutationKind.REPLACE_SYMLINK}:
            os.symlink(target, temporary, target_is_directory=True, dir_fd=parent_fd)
        else:
            os.mkdir(temporary, mode=mode, dir_fd=parent_fd)
        os.fsync(parent_fd)
        staged = _snapshot_at(path.with_name(temporary), parent_fd)
        after = replace(staged, path=path)
        if staged.kind == "regular":
            descriptor = os.open(
                temporary, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            try:
                os.utime(descriptor, ns=(staged.atime_ns, staged.mtime_ns))
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        journal = _transaction_journal(
            path, transaction_id, temporary, backup, journal_name,
            kind.value, "prepared", before, after,
        )
        _write_journal_at(parent_fd, journal_name, journal, create=True)
        journal_created = True
        if backup is not None:
            os.link(
                path.name, backup, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            backup_created = True
            os.fsync(parent_fd)
        entry = RollbackEntry(
            before, after, parent_snapshot, backup=backup, journal=journal_name,
        )
        rollback_entries.append(entry)
        try:
            os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            published = True
        except BaseException:
            try:
                actual = _snapshot_at(path, parent_fd)
            except Exception:
                actual = None
            unchanged = actual == before or (
                actual is not None and backup is not None and
                _backup_snapshot_matches(actual, before, nlink=before.nlink + 1)
            )
            if unchanged:
                rollback_entries.remove(entry)
            else:
                published = True
            raise
        journal = replace(journal, phase=JournalPhase.PUBLISHED)
        _write_journal_at(parent_fd, journal_name, journal, create=False)
        _verify_snapshot_at(after, parent_fd)
        os.fsync(parent_fd)
        return after
    finally:
        if not published:
            _cleanup_artifact(parent_fd, temporary, path, "temporary", conflicts)
            if backup_created:
                _cleanup_artifact(parent_fd, backup, path, "backup", conflicts)
            if journal_created:
                _cleanup_artifact(parent_fd, journal_name, path, "journal", conflicts)
def _publish_delete(
    parent_fd, path, expected, parent_snapshot, rollback_entries, conflicts,
):
    transaction_id = uuid.uuid4().hex
    backup = f".{path.name}.evergreen-backup-{transaction_id}"
    journal_name = f".{path.name}.evergreen-journal-{transaction_id}"
    kind = MutationKind(f"delete_{expected.kind}")
    after = PathSnapshot(path, "absent")
    if expected.kind == "regular":
        descriptor = os.open(
            path.name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd,
        )
        try:
            os.utime(descriptor, ns=(expected.atime_ns, expected.mtime_ns))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    journal = _transaction_journal(
        path, transaction_id, None, backup, journal_name,
        kind.value, "prepared", expected, after,
    )
    _write_journal_at(parent_fd, journal_name, journal, create=True)
    entry = RollbackEntry(expected, after, parent_snapshot, backup, journal_name)
    rollback_entries.append(entry)
    published = False
    try:
        try:
            os.replace(path.name, backup, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            published = True
        except BaseException:
            try:
                actual = _snapshot_at(path, parent_fd)
                backup_snapshot = _snapshot_at(path.with_name(backup), parent_fd)
            except Exception:
                actual = backup_snapshot = None
            if actual == expected and (
                backup_snapshot is None or backup_snapshot.kind == "absent"
            ):
                rollback_entries.remove(entry)
            else:
                published = True
            raise
        journal = replace(journal, phase=JournalPhase.PUBLISHED)
        _write_journal_at(parent_fd, journal_name, journal, create=False)
        _verify_snapshot_at(after, parent_fd)
        os.fsync(parent_fd)
        return after
    finally:
        if not published:
            _cleanup_artifact(parent_fd, journal_name, path, "journal", conflicts)
def _replace_descriptor_bytes(descriptor, data, path):
    os.ftruncate(descriptor, 0)
    os.lseek(descriptor, 0, os.SEEK_SET)
    remaining = memoryview(data)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError(f"short in-place write: {path}")
        remaining = remaining[written:]
    os.fsync(descriptor)
def _metadata_identity(metadata):
    return (
        _kind_from_mode(metadata.st_mode), metadata.st_dev, metadata.st_ino,
        stat.S_IMODE(metadata.st_mode), metadata.st_nlink,
        metadata.st_uid, metadata.st_gid,
    )
def _snapshot_identity(snapshot):
    return (snapshot.kind, snapshot.dev, snapshot.ino, snapshot.mode,
            snapshot.nlink, snapshot.uid, snapshot.gid)
def _transaction_journal(
    path, transaction_id, temporary, backup, journal, operation, phase,
    before, after,
):
    return JournalRecord(
        schema_version=1, transaction_id=transaction_id,
        phase=JournalPhase(phase), mutation=MutationKind(operation),
        target=path.name, temporary=temporary, backup=backup, journal=journal,
        before=before.journal_identity(), after=after.journal_identity(),
    )
def _write_journal_at(parent_fd, name, journal, *, create):
    payload = journal.encode()
    if len(payload) > MAX_STATE_BYTES:
        raise OSError("transaction journal exceeds byte limit")
    flags = os.O_WRONLY | (os.O_CREAT | os.O_EXCL if create else os.O_TRUNC)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
    try:
        _replace_descriptor_bytes(descriptor, payload, Path(name))
    finally:
        os.close(descriptor)
    os.fsync(parent_fd)
def _cleanup_artifact(parent_fd, name, path, label, conflicts):
    try:
        _remove_durable(parent_fd, name, path.parent / name, label)
    except FileNotFoundError:
        return
    except OSError as error:
        conflicts.append(f"{path}: manual recovery required; {error}")
def _restore_entry(entry):
    parent_fd = _open_directory(entry.parent)
    try:
        try:
            actual = _snapshot_at(entry.after.path, parent_fd)
        except (OSError, ValueError) as error:
            retained = (
                f"; backup retained at {_backup_path(entry)}"
                if entry.backup else ""
            )
            if entry.journal:
                retained += f"; journal retained at {entry.before.path.parent / entry.journal}"
            raise OSError(
                f"rollback postimage unavailable: {entry.after.path}{retained}: {error}"
            ) from error
        same_after = actual == entry.after or (
            entry.after.kind == "directory" and
            (actual.kind, actual.dev, actual.ino, actual.mode, actual.uid, actual.gid) ==
            (entry.after.kind, entry.after.dev, entry.after.ino,
             entry.after.mode, entry.after.uid, entry.after.gid)
        )
        if not same_after:
            backup = f"; backup retained at {_backup_path(entry)}" if entry.backup else ""
            raise OSError(
                f"preserved concurrent state; rollback postimage changed: "
                f"{entry.after.path}{backup}"
            )
        if entry.backup is not None:
            _verify_backup(parent_fd, entry)
            try:
                os.replace(
                    entry.backup, entry.before.path.name,
                    src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                )
            except BaseException as error:
                try:
                    restored = (
                        _backup_snapshot_matches(
                            _snapshot_at(entry.before.path, parent_fd), entry.before,
                        ) and
                        _snapshot_at(_backup_path(entry), parent_fd).kind == "absent"
                    )
                except Exception:
                    restored = False
                if not restored:
                    raise OSError(
                        f"backup restore failed; inspect {_backup_path(entry)}: {error}"
                    ) from error
            try:
                os.fsync(parent_fd)
            except OSError as error:
                raise OSError(
                    f"backup restored to {entry.before.path}, but directory durability "
                    f"failed; inspect former backup path {_backup_path(entry)}: {error}"
                ) from error
            _remove_journal(parent_fd, entry)
            return
        before = entry.before
        if before.kind == "absent":
            if entry.after.kind == "directory":
                try:
                    os.rmdir(entry.after.path.name, dir_fd=parent_fd)
                except OSError as error:
                    raise OSError(
                        f"preserved concurrent state; rollback directory not empty: "
                        f"{entry.after.path}"
                    ) from error
                os.fsync(parent_fd)
            else:
                _verify_snapshot_at(entry.after, parent_fd)
                os.unlink(entry.after.path.name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            _remove_journal(parent_fd, entry)
        elif before.kind == "regular":
            raise OSError(f"regular rollback lacks transaction backup: {before.path}")
        elif before.kind == "symlink":
            _atomic_link_at(parent_fd, before.path.name, before.target, entry.after)
        else:
            raise OSError(f"unsupported rollback preimage: {before.path}")
    finally:
        os.close(parent_fd)
def _commit_entry(entry):
    parent_fd = _open_directory(entry.parent)
    try:
        if entry.backup is None:
            if entry.after.kind == "directory":
                actual = _snapshot_at(entry.after.path, parent_fd)
                if (
                    actual.kind, actual.dev, actual.ino, actual.mode,
                    actual.uid, actual.gid,
                ) != (
                    entry.after.kind, entry.after.dev, entry.after.ino,
                    entry.after.mode, entry.after.uid, entry.after.gid,
                ):
                    raise OSError(
                        f"transaction directory changed: {entry.after.path}"
                    )
            else:
                _verify_snapshot_at(entry.after, parent_fd)
            _remove_journal(parent_fd, entry)
            return
        _verify_backup(parent_fd, entry)
        if entry.after.kind == "regular":
            _verify_snapshot_at(entry.after, parent_fd)
            descriptor = os.open(
                entry.after.path.name,
                os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
                getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            try:
                if _metadata_identity(os.fstat(descriptor)) != _snapshot_identity(entry.after):
                    raise OSError(f"transaction postimage changed: {entry.after.path}")
                os.utime(
                    descriptor,
                    ns=(entry.before.atime_ns, entry.before.mtime_ns),
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        _remove_durable(
            parent_fd, entry.backup, _backup_path(entry), "backup",
            kind=entry.before.kind,
        )
        _remove_journal(parent_fd, entry)
    finally:
        os.close(parent_fd)
def _verify_backup(parent_fd, entry):
    if entry.backup is None:
        raise OSError("transaction backup is missing")
    try:
        actual = _snapshot_at(entry.before.path.with_name(entry.backup), parent_fd)
    except (OSError, ValueError) as error:
        raise OSError(f"transaction backup unavailable at {_backup_path(entry)}: {error}") from error
    if not _backup_snapshot_matches(actual, entry.before):
        raise OSError(f"transaction backup changed at {_backup_path(entry)}")
    if entry.before.kind != "regular":
        return
    descriptor = os.open(
        entry.backup,
        os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        if _metadata_identity(os.fstat(descriptor)) != _snapshot_identity(entry.before):
            raise OSError(f"transaction backup changed at {_backup_path(entry)}")
        os.utime(
            descriptor, ns=(entry.before.atime_ns, entry.before.mtime_ns),
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
def _backup_snapshot_matches(actual, before, *, nlink=None):
    return (
        actual.kind == before.kind and actual.data == before.data and
        actual.target == before.target and
        (actual.dev, actual.ino) == (before.dev, before.ino) and
        actual.mode == before.mode and
        actual.nlink == (before.nlink if nlink is None else nlink) and
        (actual.uid, actual.gid) == (before.uid, before.gid) and
        (actual.atime_ns, actual.mtime_ns) == (before.atime_ns, before.mtime_ns) and
        actual.metadata_digest == before.metadata_digest
    )
def _backup_path(entry):
    return entry.before.path.parent / entry.backup
def _remove_journal(parent_fd, entry):
    if entry.journal is None:
        return
    path = entry.before.path.parent / entry.journal
    _remove_durable(parent_fd, entry.journal, path, "journal")
def _remove_durable(parent_fd, name, path, label, *, kind="regular"):
    removed = False
    try:
        _remove_kind(parent_fd, name, kind)
        removed = True
    except FileNotFoundError:
        removed = True
    except OSError as error:
        try:
            removed = _snapshot_at(Path(path), parent_fd).kind == "absent"
        except Exception:
            removed = False
        if not removed:
            raise OSError(f"{label} cleanup failed at {path}: {error}") from error
    try:
        os.fsync(parent_fd)
    except OSError as error:
        state = "removal succeeded" if removed else "state is ambiguous"
        raise OSError(
            f"{label} {state} but directory durability failed; inspect former "
            f"path {path}: {error}"
        ) from error
