"""Bounded journal discovery and crash-idempotent transaction recovery."""

from dataclasses import replace
import os
from pathlib import Path
import time

from .host_snapshot import open_directory, snapshot, snapshot_at
from .host_types import (
    JournalPhase, JournalRecord, MutationKind, PathSnapshot, TransactionCommit,
)

READ_ELAPSED_LIMIT_SECONDS = 3
MAX_MATCHING_ARTIFACTS = 128
MAX_SCANNED_ENTRIES = 4096
MAX_TOTAL_SCANNED_ENTRIES = 8192
MAX_SCANNED_DIRECTORIES = 8
MAX_TRANSACTION_MARKERS = 128


class CommitPublishedError(OSError):
    """The commit marker is visible but its directory fsync was inconclusive."""


def recover_transactions(selected, open_parent=None):
    errors = []
    failed_participants = set()
    coordinator = min((status.root.parent for status in selected), key=str)
    commit_parent = (
        open_parent(coordinator) if open_parent else
        open_directory(snapshot(coordinator, allow_directory=True))
    )
    budget = _scan_budget()
    try:
        try:
            committed, updates = read_transaction_commits(commit_parent, budget)
        except (OSError, ValueError) as error:
            return [f"transaction commit scan failed: {error}"]
        targets_by_parent = {}
        for status in selected:
            for target in (
                status.instructions, status.ownership, status.skill,
                status.skill.parent,
            ):
                targets_by_parent.setdefault(target.parent, []).append((status, target))
        for parent_path, items in sorted(
            targets_by_parent.items(),
            key=lambda item: len(item[0].parts), reverse=True,
        ):
            try:
                parent = (
                    open_parent(parent_path)
                    if open_parent else open_directory(
                        snapshot(parent_path, allow_directory=True)
                    )
                )
            except (FileNotFoundError, NotADirectoryError):
                continue
            try:
                try:
                    discovered = discover_parent_artifacts(
                        parent, [target for _status, target in items], budget,
                    )
                except (OSError, TimeoutError) as error:
                    for status, _target in items:
                        failed_participants.add(status.name)
                    errors.append(f"artifact scan failed in {parent_path}: {error}")
                    continue
                for status, target in items:
                    status_committed = frozenset(
                        transaction_id
                        for transaction_id, record in committed.items()
                        if status.name in record.participants
                    )
                    error = recover_target_groups(
                        parent, target, discovered[target], status_committed,
                    )
                    if error:
                        errors.append(f"{status.name}: {error}")
                        failed_participants.add(status.name)
            finally:
                os.close(parent)
        if not errors:
            remove_transaction_updates(commit_parent, updates)
        selected_names = {status.name for status in selected}
        for transaction_id, record in committed.items():
            completed = selected_names - failed_participants
            pending = tuple(
                participant for participant in record.pending
                if participant not in completed
            )
            if pending == record.pending:
                continue
            if not pending:
                try:
                    remove_transaction_commit(commit_parent, transaction_id)
                except OSError as error:
                    errors.append(f"transaction commit cleanup failed: {error}")
            else:
                try:
                    write_transaction_commit(
                        commit_parent, transaction_id, record.participants,
                        pending, create=False,
                    )
                except OSError as error:
                    errors.append(f"transaction commit update failed: {error}")
        return errors
    finally:
        os.close(commit_parent)


def recover_target_artifacts(
    parent_fd, target, committed=frozenset(), *, budget=None,
):
    budget = budget or _scan_budget()
    try:
        groups = discover_parent_artifacts(parent_fd, [target], budget)[target]
    except (OSError, TimeoutError) as error:
        return f"artifact scan limit exceeded in {target.parent}: {error}"
    return recover_target_groups(parent_fd, target, groups, committed)


def discover_parent_artifacts(parent_fd, targets, budget):
    _consume_scan(budget, directory=True)
    targets = tuple(dict.fromkeys(targets))
    discovered = {target: {} for target in targets}
    artifact_count = 0
    with os.scandir(parent_fd) as entries:
        for examined, entry in enumerate(entries, start=1):
            _consume_scan(budget)
            if examined > MAX_SCANNED_ENTRIES:
                raise OSError("transaction artifact scan limit exceeded")
            for target in targets:
                parsed = JournalRecord.artifact_name(target.name, entry.name)
                if not parsed:
                    continue
                artifact_count += 1
                if artifact_count > MAX_MATCHING_ARTIFACTS:
                    raise OSError("transaction artifact scan limit exceeded")
                artifact_kind, transaction_id = parsed
                discovered[target].setdefault(transaction_id, {})[
                    artifact_kind
                ] = entry.name
    return discovered


def recover_target_groups(parent_fd, target, groups, committed=frozenset()):
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
        update_name = artifacts.get("journal_update")
        record_name = update_name or journal_name
        journal_item = snapshot_at(target.with_name(record_name), parent_fd)
        _verify_control_file(journal_item, "transaction journal")
        if update_name is not None:
            _verify_control_file(
                snapshot_at(target.with_name(journal_name), parent_fd),
                "transaction journal",
            )
        record = JournalRecord.parse(journal_item.data)
        if not record.names_match(target.name, transaction_id, artifacts):
            return manual_artifact_error(paths)
        if update_name is not None:
            os.replace(
                update_name, journal_name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        recover_record(
            parent_fd, target, record, artifacts,
            committed=transaction_id in committed,
        )
        return None
    except (OSError, TypeError, ValueError, KeyError):
        return manual_artifact_error(paths)


def recover_record(parent_fd, target, record, artifacts, *, committed=False):
    live = snapshot_at(target, parent_fd)
    staged = _artifact_snapshot(parent_fd, target, artifacts.get("temporary"))
    backup = _artifact_snapshot(parent_fd, target, artifacts.get("backup"))
    creates = {
        MutationKind.CREATE_REGULAR, MutationKind.CREATE_SYMLINK,
        MutationKind.CREATE_DIRECTORY,
    }
    replaces = {MutationKind.REPLACE_REGULAR, MutationKind.REPLACE_SYMLINK}
    if committed:
        _finish_committed(parent_fd, target, record, live, staged, backup)
        return
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
            if backup.kind != "absent":
                remove_kind(parent_fd, record.backup, backup.kind)
            remove_kind(parent_fd, record.temporary, staged.kind)
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


def _finish_committed(parent_fd, target, record, live, staged, backup):
    if staged.kind != "absent":
        raise ValueError("committed transaction retained a staged path")
    after_matches = journal_snapshot_matches(live, record.after)
    if live.kind == "directory":
        after_matches = all(
            live.journal_identity().get(field) == record.after.get(field)
            for field in ("kind", "dev", "ino", "mode", "uid", "gid")
        )
    if not after_matches:
        raise ValueError("committed postimage changed")
    if backup.kind != "absent":
        if not journal_snapshot_matches(backup, record.before):
            raise ValueError("committed backup changed")
        remove_kind(parent_fd, record.backup, backup.kind)
        os.fsync(parent_fd)
    try:
        os.unlink(record.journal, dir_fd=parent_fd)
    except FileNotFoundError:
        pass
    os.fsync(parent_fd)


def transaction_commit_name(transaction_id):
    return f".evergreen-transaction-{transaction_id}.json"


def transaction_commit_update_name(transaction_id):
    return transaction_commit_name(transaction_id) + ".update"


def write_transaction_commit(
    parent_fd, transaction_id, participants, pending=None, *, create=True,
):
    participants = tuple(sorted(participants))
    pending = participants if pending is None else tuple(sorted(pending))
    record = TransactionCommit(1, transaction_id, participants, pending)
    name = transaction_commit_name(transaction_id)
    update = transaction_commit_update_name(transaction_id)
    if create and snapshot_at(Path(name), parent_fd).kind != "absent":
        raise FileExistsError(name)
    published = False
    temporary_created = False
    try:
        descriptor = os.open(
            update, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
            getattr(os, "O_NOFOLLOW", 0),
            0o600, dir_fd=parent_fd,
        )
        temporary_created = True
        try:
            os.fchmod(descriptor, 0o600)
            payload = record.encode()
            remaining = memoryview(payload)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("short transaction commit write")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except BaseException:
        if temporary_created:
            try:
                os.unlink(update, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError:
                pass
        raise
    try:
        os.replace(update, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        published = True
        os.fsync(parent_fd)
    except OSError as error:
        if not published:
            try:
                final = snapshot_at(Path(name), parent_fd)
                published = (
                    _control_file_is_safe(final) and
                    TransactionCommit.parse(final.data) == record and
                    snapshot_at(Path(update), parent_fd).kind == "absent"
                )
            except (OSError, TypeError, ValueError):
                published = False
        if published:
            raise CommitPublishedError(
                f"transaction commit publication durability is uncertain: {error}"
            ) from error
        try:
            os.unlink(update, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError:
            pass
        raise


def read_transaction_commits(parent_fd, budget=None):
    committed = {}
    updates = []
    budget = budget or _scan_budget()
    _consume_scan(budget, directory=True)
    with os.scandir(parent_fd) as entries:
        for examined, entry in enumerate(entries, start=1):
            _consume_scan(budget)
            if examined > MAX_SCANNED_ENTRIES:
                raise OSError("transaction commit scan limit exceeded")
            prefix, suffix = ".evergreen-transaction-", ".json"
            update_suffix = suffix + ".update"
            is_update = entry.name.endswith(update_suffix)
            if not entry.name.startswith(prefix) or not (
                entry.name.endswith(suffix) or is_update
            ):
                continue
            if len(committed) + len(updates) >= MAX_TRANSACTION_MARKERS:
                raise OSError("transaction commit marker limit exceeded")
            item = snapshot_at(Path(entry.name), parent_fd)
            _verify_control_file(item, "transaction commit")
            if is_update:
                transaction_id = entry.name[len(prefix):-len(update_suffix)]
                if not _valid_transaction_id(transaction_id):
                    raise ValueError("transaction commit identity mismatch")
                updates.append(entry.name)
                continue
            record = TransactionCommit.parse(item.data)
            if entry.name != transaction_commit_name(record.transaction_id):
                raise ValueError("transaction commit identity mismatch")
            committed[record.transaction_id] = record
    return committed, tuple(updates)


def remove_transaction_commit(parent_fd, transaction_id):
    try:
        os.unlink(transaction_commit_name(transaction_id), dir_fd=parent_fd)
    except FileNotFoundError:
        return
    os.fsync(parent_fd)


def remove_transaction_updates(parent_fd, updates):
    for name in updates:
        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            continue
    if updates:
        os.fsync(parent_fd)


def _scan_budget():
    return {
        "deadline": time.monotonic() + READ_ELAPSED_LIMIT_SECONDS,
        "entries": 0,
        "directories": 0,
    }


def _consume_scan(budget, *, directory=False):
    if directory:
        budget["directories"] += 1
        if budget["directories"] > MAX_SCANNED_DIRECTORIES:
            raise OSError("transaction directory scan limit exceeded")
    else:
        budget["entries"] += 1
        if budget["entries"] > MAX_TOTAL_SCANNED_ENTRIES:
            raise OSError("transaction aggregate scan limit exceeded")
    if time.monotonic() > budget["deadline"]:
        raise TimeoutError("transaction recovery exceeded elapsed-time limit")


def _valid_transaction_id(value):
    return (
        isinstance(value, str) and len(value) == 32 and
        all(char in "0123456789abcdef" for char in value)
    )


def _verify_control_file(item, label):
    if not _control_file_is_safe(item):
        raise ValueError(f"unsafe {label}")


def _control_file_is_safe(item):
    return (
        item.kind == "regular" and item.nlink == 1 and item.mode == 0o600 and
        item.uid == os.getuid()
    )


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
    write_name = name if create else name + ".update"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(
        write_name, flags | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=parent_fd,
    )
    try:
        os.fchmod(descriptor, 0o600)
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
