"""Bounded journal discovery and crash-idempotent transaction recovery."""

from dataclasses import replace
import os
from pathlib import Path
import time
import uuid

from .host_snapshot import kind, open_directory, snapshot, snapshot_at
from .host_types import JournalPhase, JournalRecord, MutationKind, PathSnapshot

READ_ELAPSED_LIMIT_SECONDS = 3
MAX_MATCHING_ARTIFACTS = 128
MAX_SCANNED_ENTRIES = 4096


def recover_transactions(selected):
    errors = []
    for status in selected:
        for target in (status.instructions, status.ownership, status.skill, status.skill.parent):
            if kind(target.parent) != "directory":
                continue
            parent = open_directory(snapshot(target.parent, allow_directory=True))
            try:
                error = recover_target_artifacts(parent, target)
                if error:
                    errors.append(f"{status.name}: {error}")
            finally:
                os.close(parent)
    return errors


def recover_target_artifacts(parent_fd, target):
    deadline = time.monotonic() + READ_ELAPSED_LIMIT_SECONDS
    groups = {}
    try:
        with os.scandir(parent_fd) as entries:
            for examined, entry in enumerate(entries, start=1):
                if examined > MAX_SCANNED_ENTRIES or time.monotonic() > deadline:
                    return _scan_error(target)
                parsed = JournalRecord.artifact_name(target.name, entry.name)
                if not parsed:
                    continue
                if sum(len(group) for group in groups.values()) >= MAX_MATCHING_ARTIFACTS:
                    return _scan_error(target)
                artifact_kind, transaction_id = parsed
                groups.setdefault(transaction_id, {})[artifact_kind] = entry.name
    except OSError as error:
        return f"artifact scan failed in {target.parent}: {error}"
    paths = sorted(str(target.parent / name) for group in groups.values() for name in group.values())
    if not groups:
        return None
    if len(groups) != 1 or len(paths) > 16:
        return manual_artifact_error(paths[:16])
    transaction_id, artifacts = next(iter(groups.items()))
    journal_name = artifacts.get("journal")
    if journal_name is None:
        return manual_artifact_error(paths)
    try:
        record = JournalRecord.parse(snapshot_at(target.with_name(journal_name), parent_fd).data)
        if not record.names_match(target.name, transaction_id, artifacts):
            return manual_artifact_error(paths)
        recover_record(parent_fd, target, record, artifacts)
        return None
    except (OSError, TypeError, ValueError, KeyError):
        return manual_artifact_error(paths)


def _scan_error(target):
    return f"artifact scan limit exceeded in {target.parent}; inspect manually"


def recover_record(parent_fd, target, record, artifacts):
    live = snapshot_at(target, parent_fd)
    staged = _artifact_snapshot(parent_fd, target, artifacts.get("temporary"))
    backup = _artifact_snapshot(parent_fd, target, artifacts.get("backup"))
    creates = {
        MutationKind.CREATE_REGULAR, MutationKind.CREATE_SYMLINK,
        MutationKind.CREATE_DIRECTORY,
    }
    replaces = {MutationKind.REPLACE_REGULAR, MutationKind.REPLACE_SYMLINK}
    if record.phase == JournalPhase.RECOVERING and _recovery_finished(
        live, backup, record, creates, replaces,
    ):
        if staged.kind != "absent":
            remove_kind(parent_fd, record.temporary, staged.kind)
        os.unlink(record.journal, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return
    write_journal_at(
        parent_fd, record.journal, replace(record, phase=JournalPhase.RECOVERING),
        create=False,
    )
    if record.mutation in creates | replaces:
        if staged.kind != "absent":
            if not journal_snapshot_matches(staged, record.after):
                raise ValueError("staged postimage changed")
            if record.mutation in creates:
                if live.kind != "absent" or backup.kind != "absent":
                    raise ValueError("create preimage changed")
            else:
                links = 2 if backup.kind != "absent" else 1
                if not journal_snapshot_matches(live, record.before, nlink=links):
                    raise ValueError("replace preimage changed")
                if backup.kind != "absent" and (
                    (live.dev, live.ino) != (backup.dev, backup.ino) or backup.nlink != 2
                ):
                    raise ValueError("replace backup changed")
            remove_kind(parent_fd, record.temporary, staged.kind)
            if backup.kind != "absent":
                remove_kind(parent_fd, record.backup, backup.kind)
        else:
            after_matches = journal_snapshot_matches(live, record.after)
            if live.kind == "directory":
                after_matches = all(
                    live.journal_identity().get(field) == record.after.get(field)
                    for field in ("kind", "dev", "ino", "mode", "uid", "gid")
                )
            if not after_matches:
                raise ValueError("published postimage changed")
            if record.mutation in creates:
                remove_kind(parent_fd, target.name, live.kind)
            else:
                if not journal_snapshot_matches(backup, record.before):
                    raise ValueError("replace backup changed")
                os.replace(record.backup, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    elif backup.kind == "absent":
        if not journal_snapshot_matches(live, record.before):
            raise ValueError("delete preimage changed")
    else:
        if live.kind != "absent" or not journal_snapshot_matches(backup, record.before):
            raise ValueError("delete backup changed")
        os.replace(record.backup, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.unlink(record.journal, dir_fd=parent_fd)
    os.fsync(parent_fd)


def _recovery_finished(live, backup, record, creates, replaces):
    if record.mutation in creates:
        return live.kind == "absent" and backup.kind == "absent"
    return backup.kind == "absent" and journal_snapshot_matches(live, record.before)


def _artifact_snapshot(parent_fd, target, name):
    return PathSnapshot(target, "absent") if name is None else snapshot_at(target.with_name(name), parent_fd)


def manual_artifact_error(paths):
    return ("transaction artifacts require manual recovery: " + ", ".join(paths) +
            "; inspect the journal and restore the named backup before removing artifacts")


def remove_kind(parent_fd, name, item_kind):
    (os.rmdir if item_kind == "directory" else os.unlink)(name, dir_fd=parent_fd)


def journal_snapshot_matches(item, expected, *, nlink=None):
    if not isinstance(expected, dict) or (nlink is not None and item.nlink != nlink):
        return False
    actual, expected = item.journal_identity(), dict(expected)
    if nlink is not None:
        expected["nlink"] = nlink
    return actual == expected


def write_journal_at(parent_fd, name, journal, *, create):
    payload = journal.encode()
    if len(payload) > 4096:
        raise OSError("transaction journal exceeds byte limit")
    write_name = name if create else name + ".update-" + uuid.uuid4().hex
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(
        write_name, flags | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd,
    )
    try:
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        remaining = memoryview(payload)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError(f"short journal write: {Path(name)}")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    if not create:
        os.replace(write_name, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
    os.fsync(parent_fd)
