"""Validation and artifact cleanup for durably committed host transactions."""

import os
from pathlib import Path

from .host_journal import remove_kind
from .host_snapshot import open_directory, snapshot_at, verify_snapshot_at


def validate_entry(entry, open_parent=None, verify_binding=lambda: None):
    verify_binding()
    parent_fd = (
        open_parent(entry.parent.path, entry.parent)
        if open_parent else open_directory(entry.parent)
    )
    try:
        if entry.backup is None:
            if entry.after.kind == "directory":
                actual = snapshot_at(entry.after.path, parent_fd)
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
                verify_snapshot_at(entry.after, parent_fd)
            verify_binding()
            return
        verify_backup(parent_fd, entry)
        if entry.after.kind == "regular":
            verify_snapshot_at(entry.after, parent_fd)
            descriptor = os.open(
                entry.after.path.name,
                os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
                getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            try:
                if not entry.after.matches_stat(os.fstat(descriptor)):
                    raise OSError(
                        f"transaction postimage changed: {entry.after.path}"
                    )
                os.utime(
                    descriptor,
                    ns=(entry.before.atime_ns, entry.before.mtime_ns),
                )
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        verify_binding()
    finally:
        os.close(parent_fd)


def cleanup_entry(entry, open_parent=None):
    parent_fd = (
        open_parent(entry.parent.path, entry.parent)
        if open_parent else open_directory(entry.parent)
    )
    try:
        if entry.backup is not None:
            remove_durable(
                parent_fd, entry.backup, backup_path(entry), "backup",
                kind=entry.before.kind,
            )
        remove_journal(parent_fd, entry)
    finally:
        os.close(parent_fd)


def verify_backup(parent_fd, entry):
    if entry.backup is None:
        raise OSError("transaction backup is missing")
    try:
        actual = snapshot_at(entry.before.path.with_name(entry.backup), parent_fd)
    except (OSError, ValueError) as error:
        raise OSError(
            f"transaction backup unavailable at {backup_path(entry)}: {error}"
        ) from error
    if not entry.before.matches(actual):
        raise OSError(f"transaction backup changed at {backup_path(entry)}")
    if entry.before.kind != "regular":
        return
    descriptor = os.open(
        entry.backup,
        os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
        getattr(os, "O_NOFOLLOW", 0),
        dir_fd=parent_fd,
    )
    try:
        if not entry.before.matches_stat(os.fstat(descriptor)):
            raise OSError(f"transaction backup changed at {backup_path(entry)}")
        os.utime(
            descriptor, ns=(entry.before.atime_ns, entry.before.mtime_ns),
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def backup_path(entry):
    return entry.before.path.parent / entry.backup


def remove_journal(parent_fd, entry):
    if entry.journal is None:
        return
    path = entry.before.path.parent / entry.journal
    remove_durable(parent_fd, entry.journal, path, "journal")


def remove_durable(parent_fd, name, path, label, *, kind="regular"):
    removed = False
    try:
        remove_kind(parent_fd, name, kind)
        removed = True
    except FileNotFoundError:
        removed = True
    except OSError as error:
        try:
            removed = snapshot_at(Path(path), parent_fd).kind == "absent"
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
