"""Reversible Claude and Codex host integration."""
from dataclasses import replace
import os
from pathlib import Path
import stat
import uuid
from .host_types import (JournalPhase, JournalRecord, Mutation, MutationKind, OperationResult,
    PathSnapshot, RollbackEntry,
)
from .host_metadata import clone as _clone_regular_metadata
from .host_lock import acquire as _lock_hosts, release as _unlock_hosts
from .host_journal import (
    recover_transactions as _recover_transactions,
    remove_kind as _remove_kind,
    write_journal_at as _write_journal_at,
)
from .host_snapshot import (
    OWNERSHIP_FILE, normalized_lexical_path as _normalized_lexical_path,
    normalized_snapshot_target as _normalized_snapshot_target,
    open_directory as _open_directory, snapshot_at as _snapshot_at,
    verify_managed_root_binding as _verify_managed_root_binding,
    verify_pinned_roots as _verify_pinned_roots,
    verify_preflight as _verify_preflight, verify_snapshot_at as _verify_snapshot_at,
)

class TransactionEngine:
    def __init__(self, selected, locks, roots=None, authorization=None):
        self.selected, self._locks = tuple(selected), locks
        self._roots = roots or {}
        self._authorization = authorization or {}
    @classmethod
    def acquire(cls, selected, authorization):
        locks, roots, error = _lock_hosts(selected, authorization)
        return (None, error) if error else (
            cls(selected, locks, roots, authorization), None
        )

    def recover(self):
        self.verify_roots()
        result = _recover_transactions(self.selected, self.open_parent)
        self.verify_roots()
        return result

    def apply(self, plans, dry_run, captured):
        return _apply(plans, dry_run, captured, self.open_parent, self.verify_roots)
    def verify_roots(self):
        if self._roots:
            _verify_pinned_roots(
                self.selected, self._authorization, self._roots
            )
    def open_parent(self, path, expected=None):
        path = Path(path)
        matches = [root for root in self._roots if path == root or root in path.parents]
        if not matches:
            raise OSError(f"transaction path is outside locked hosts: {path}")
        root = max(matches, key=lambda item: len(item.parts))
        descriptor = os.dup(self._roots[root])
        try:
            current = root
            for part in path.relative_to(root).parts:
                child = os.open(
                    part,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
                    getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
                current = current / part
            if expected is not None:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISDIR(metadata.st_mode) or
                    (metadata.st_dev, metadata.st_ino, stat.S_IMODE(metadata.st_mode)) !=
                    (expected.dev, expected.ino, expected.mode)
                ):
                    raise OSError(f"transaction directory changed: {path}")
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise
    def close(self):
        locks, self._locks = self._locks, []
        if not locks:
            return None
        errors = _unlock_hosts(locks)
        return None if not errors else "error: host lock cleanup failed: " + "; ".join(errors)

def _apply(plans, dry_run, captured, open_parent=None, verify_roots=lambda: None):
    open_parent = open_parent or (
        lambda path, expected=None: _open_directory(expected or captured[path])
    )
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
    mutation_paths = {path for _action, _detail, path, _value in mutations}
    guard_snapshots = {
        path: snapshot for path, snapshot in captured.items()
        if path not in mutation_paths and
        not any(path in mutation_path.parents for mutation_path in mutation_paths)
    }
    try:
        verify_roots()
        for action, _detail, path, value in mutations:
            parent_fd = _prepare_parent(
                path.parent, captured, rollback_entries, conflicts, open_parent
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
                verify_roots()
            finally:
                os.close(parent_fd)
        _verify_preflight(guard_snapshots)
        for status in dict.fromkeys(status for status, _plan in plans):
            _verify_managed_root_binding(status)
        verify_roots()
    except Exception as error:
        rollback_errors = []
        for entry in reversed(rollback_entries):
            try:
                _restore_entry(entry, open_parent)
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
            _commit_entry(entry, open_parent)
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

def _prepare_parent(path, captured, rollback_entries, conflicts, open_parent):
    snapshot = captured[path]
    if snapshot.kind == "directory":
        return open_parent(path, snapshot)
    if snapshot.kind != "absent":
        raise OSError(f"transaction parent is {snapshot.kind}: {path}")
    grandparent = captured[path.parent]
    descriptor = open_parent(path.parent, grandparent)
    created = None
    child = None
    try:
        _verify_snapshot_at(snapshot, descriptor)
        created = _publish_path(
            descriptor, Mutation(
                MutationKind.CREATE_DIRECTORY, path, snapshot, grandparent, mode=0o755,
            ), rollback_entries, conflicts,
        )
        child = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=descriptor,
        )
        metadata = os.fstat(child)
        if (
            not stat.S_ISDIR(metadata.st_mode) or
            (metadata.st_dev, metadata.st_ino, stat.S_IMODE(metadata.st_mode)) !=
            (created.dev, created.ino, created.mode)
        ):
            raise OSError(f"transaction directory changed: {path}")
    except Exception as error:
        if child is not None:
            os.close(child)
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
    return child

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
            parent_fd, Mutation(
                kind, path, expected, parent_snapshot, data=value, mode=mode,
            ), rollback_entries, conflicts,
        )
    elif action == "link":
        kind = (
            MutationKind.CREATE_SYMLINK if expected.kind == "absent"
            else MutationKind.REPLACE_SYMLINK
        )
        return _publish_path(
            parent_fd, Mutation(
                kind, path, expected, parent_snapshot, target=value,
            ), rollback_entries, conflicts,
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
    parent_fd, mutation, rollback_entries, conflicts,
):
    path, kind, before = mutation.path, mutation.kind, mutation.before
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
                temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mutation.mode,
                dir_fd=parent_fd,
            )
            try:
                _replace_descriptor_bytes(descriptor, mutation.data, path)
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
                    os.fchmod(descriptor, mutation.mode)
            finally:
                os.close(descriptor)
        elif kind in {MutationKind.CREATE_SYMLINK, MutationKind.REPLACE_SYMLINK}:
            os.symlink(mutation.target, temporary, target_is_directory=True, dir_fd=parent_fd)
        else:
            os.mkdir(temporary, mode=mutation.mode, dir_fd=parent_fd)
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
            mutation, after,
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
            before, after, mutation.parent, backup=backup, journal=journal_name,
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
                before.matches(actual, nlink=before.nlink + 1)
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
    mutation = Mutation(kind, path, expected, parent_snapshot)
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
        path, transaction_id, None, backup, journal_name, mutation, after,
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

def _transaction_journal(
    path, transaction_id, temporary, backup, journal, mutation, after,
):
    return JournalRecord(
        schema_version=1, transaction_id=transaction_id,
        phase=JournalPhase.PREPARED, mutation=mutation.kind,
        target=path.name, temporary=temporary, backup=backup, journal=journal,
        before=mutation.before.journal_identity(), after=after.journal_identity(),
    )

def _cleanup_artifact(parent_fd, name, path, label, conflicts):
    try:
        _remove_durable(parent_fd, name, path.parent / name, label)
    except FileNotFoundError:
        return
    except OSError as error:
        conflicts.append(f"{path}: manual recovery required; {error}")

def _restore_entry(entry, open_parent=None):
    parent_fd = (
        open_parent(entry.parent.path, entry.parent)
        if open_parent else _open_directory(entry.parent)
    )
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
                entry.before.matches(_snapshot_at(entry.before.path, parent_fd)) and
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
            raise OSError(f"symlink rollback lacks transaction backup: {before.path}")
        else:
            raise OSError(f"unsupported rollback preimage: {before.path}")
    finally:
        os.close(parent_fd)

def _commit_entry(entry, open_parent=None):
    parent_fd = (
        open_parent(entry.parent.path, entry.parent)
        if open_parent else _open_directory(entry.parent)
    )
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
                if not entry.after.matches_stat(os.fstat(descriptor)):
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
    if not entry.before.matches(actual):
        raise OSError(f"transaction backup changed at {_backup_path(entry)}")
    if entry.before.kind != "regular":
        return
    descriptor = os.open(
        entry.backup,
        os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        if not entry.before.matches_stat(os.fstat(descriptor)):
            raise OSError(f"transaction backup changed at {_backup_path(entry)}")
        os.utime(
            descriptor, ns=(entry.before.atime_ns, entry.before.mtime_ns),
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

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
