"""Reversible Claude and Codex host integration."""

import ast
from dataclasses import asdict, dataclass, field
import errno
import json
import os
from pathlib import Path
import stat
import time
import uuid

try:
    import posix as _posix
except ImportError:  # pragma: no cover - POSIX hosts are required by the CLI.
    _posix = None


BEGIN_MARKER = "<!-- evergreen:begin -->"
END_MARKER = "<!-- evergreen:end -->"
OWNERSHIP_FILE = ".evergreen-owned.json"
MAX_STATE_BYTES = 4096
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTRUCTION_BYTES = 1024 * 1024
MAX_COMMAND_BYTES = 1024 * 1024
MAX_XATTRS = 100
MAX_XATTR_BYTES = 1024 * 1024
# Elapsed-time detection only: regular-file syscalls are not portably interruptible.
READ_ELAPSED_LIMIT_SECONDS = 3


@dataclass(frozen=True)
class HostStatus:
    name: str
    present: bool
    root: Path
    instructions: Path
    skill: Path
    ownership: Path
    problem: str | None = None


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    messages: tuple[str, ...]


@dataclass(frozen=True)
class Ownership:
    schema_version: int
    host: str
    plugin_root: str
    instruction_existed: bool
    instruction_separator: bool
    skill_target: str


@dataclass(frozen=True)
class PathSnapshot:
    path: Path
    kind: str
    data: bytes | None = None
    target: str | None = None
    mode: int | None = None
    dev: int | None = None
    ino: int | None = None
    nlink: int | None = None
    uid: int | None = None
    gid: int | None = None
    atime_ns: int | None = field(default=None, compare=False)
    mtime_ns: int | None = field(default=None, compare=False)


@dataclass(frozen=True)
class RollbackEntry:
    before: PathSnapshot
    after: PathSnapshot
    parent: PathSnapshot
    backup: str | None = None


def detect_hosts(home: Path) -> list[HostStatus]:
    home = Path(home)
    return [
        _status(home, "claude", ".claude", "CLAUDE.md"),
        _status(home, "codex", ".codex", "AGENTS.md"),
    ]


def install(home: Path, plugin_root: Path, host: str, dry_run: bool = False) -> OperationResult:
    canonical, canonical_messages = _canonical(plugin_root)
    if canonical is None:
        return OperationResult(False, tuple(canonical_messages))
    root, target, _version = canonical
    selected, error = _select(home, host)
    if error:
        return OperationResult(False, (error,))
    errors = _host_errors(selected)
    if errors:
        return OperationResult(False, tuple(errors))
    try:
        captured = _capture_preflight(selected)
    except Exception as error:
        return OperationResult(False, (f"error: transaction preflight failed: {error}",))
    states = {}
    for status in selected:
        state, state_error = _ownership_from_snapshot(status, captured[status.ownership])
        states[status.name] = state
        if state_error:
            errors.append(f"{status.name}: {state_error}")
    if errors:
        return OperationResult(False, tuple(errors))

    block = _block(root)
    plans = []
    for status in selected:
        state = states[status.name]
        host_plans, plan_errors = _install_plans(
            status, state, root, target, block, captured
        )
        plans.extend((status, plan) for plan in host_plans)
        errors.extend(f"{status.name}: refusing {message}" for message in plan_errors)
    if errors:
        return OperationResult(False, tuple(errors))
    return _apply(plans, dry_run, captured)


def uninstall(home: Path, host: str, dry_run: bool = False) -> OperationResult:
    selected, error = _select(home, host)
    if error:
        return OperationResult(False, (error,))
    errors = _host_errors(selected)
    if errors:
        return OperationResult(False, tuple(errors))
    try:
        captured = _capture_preflight(selected)
    except Exception as error:
        return OperationResult(False, (f"error: transaction preflight failed: {error}",))
    states = {}
    for status in selected:
        state, state_error = _ownership_from_snapshot(status, captured[status.ownership])
        states[status.name] = state
        if state_error:
            errors.append(f"{status.name}: {state_error}")
    if errors:
        return OperationResult(False, tuple(errors))

    plans = []
    for status in selected:
        host_plans, plan_errors = _uninstall_plans(
            status, states[status.name], captured
        )
        plans.extend((status, plan) for plan in host_plans)
        errors.extend(f"{status.name}: refusing {message}" for message in plan_errors)
    if errors:
        return OperationResult(False, tuple(errors))
    return _apply(plans, dry_run, captured)


def doctor(home: Path, plugin_root: Path, host: str = "all") -> OperationResult:
    canonical, messages = _canonical(plugin_root)
    healthy = canonical is not None
    selected, error = _select(home, host)
    if error:
        return OperationResult(False, tuple(messages + [error]))
    host_errors = _host_errors(selected)
    if host_errors:
        return OperationResult(False, tuple(messages + host_errors))

    root = canonical[0] if canonical else _absolute(plugin_root)
    expected_skill = canonical[1] if canonical else root / "skills" / "evergreen"
    expected_block = _block(root)
    if canonical:
        command_error = _validate_python_command(root / "bin" / "evergreen")
        if command_error:
            healthy = False
            messages.append(f"error command static validation failed: {command_error}")
        else:
            messages.append("ok command static validation")
    for status in selected:
        state, state_error = _load_ownership(status)
        if state_error:
            healthy = False
            messages.append(f"error {status.name} {state_error}")
            continue
        if state is None:
            healthy = False
            messages.append(f"error {status.name} missing ownership record")
            continue
        if state.plugin_root != str(root) or state.skill_target != str(expected_skill):
            healthy = False
            messages.append(f"error {status.name} stale ownership record")

        instruction_state, owned = _instruction_state(status.instructions)
        if instruction_state == "owned" and owned == expected_block:
            messages.append(f"ok {status.name} owned instructions")
        elif instruction_state == "owned":
            healthy = False
            messages.append(f"error {status.name} stale owned instructions")
        else:
            healthy = False
            messages.append(f"error {status.name} {instruction_state} instructions")

        skill_kind = _kind(status.skill)
        if skill_kind == "symlink":
            if not status.skill.exists():
                healthy = False
                messages.append(f"error {status.name} broken skill link")
            elif status.skill.resolve() != expected_skill:
                healthy = False
                messages.append(f"error {status.name} stale skill link")
            else:
                messages.append(f"ok {status.name} skill link")
        else:
            healthy = False
            messages.append(f"error {status.name} {skill_kind} skill link")
    return OperationResult(healthy, tuple(messages))


def _canonical(plugin_root):
    lexical_root = _normalized_lexical_path(Path(plugin_root))
    errors = []
    if _kind(lexical_root) != "directory":
        return None, [f"error canonical plugin root must be a real directory: {lexical_root}"]
    root = lexical_root.resolve()
    required_directories = (
        root / ".claude-plugin", root / ".codex-plugin", root / "skills",
        root / "skills" / "evergreen", root / "bin",
    )
    for path in required_directories:
        if _kind(path) != "directory":
            errors.append(f"error canonical directory missing or unsafe: {path}")
    required_files = (
        root / "AGENTS.md", root / "skills" / "evergreen" / "SKILL.md",
        root / "bin" / "evergreen",
    )
    for path in required_files:
        if _kind(path) != "regular":
            errors.append(f"error canonical file missing or unsafe: {path}")
    command = root / "bin" / "evergreen"
    if _kind(command) == "regular" and not os.access(command, os.X_OK):
        errors.append(f"error canonical command unavailable: {command}")

    versions = []
    for name in (".claude-plugin", ".codex-plugin"):
        path = root / name / "plugin.json"
        if _kind(path) != "regular":
            errors.append(f"error canonical manifests missing or unsafe: {path}")
            continue
        try:
            value = json.loads(_read_regular_bounded(
                path, MAX_MANIFEST_BYTES, "canonical manifest"
            ))
            version = value.get("version") if isinstance(value, dict) else None
            if not isinstance(version, str) or not version:
                raise ValueError("manifest version must be a non-empty string")
            versions.append(version)
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            errors.append(f"error canonical manifests {path}: {error}")
    if len(versions) == 2 and versions[0] != versions[1]:
        errors.append("error canonical manifest versions differ")
    if errors:
        return None, errors
    version = versions[0]
    return (root, root / "skills" / "evergreen", version), [
        f"ok canonical version {version}; manifests agree",
        "ok canonical rules",
        "ok command available",
    ]


def _status(home, name, directory, instruction_name):
    root = home / directory
    kind = _kind(root)
    problem = None if kind in ("absent", "directory") else f"host root is {kind}, not a directory"
    return HostStatus(
        name=name,
        present=kind != "absent",
        root=root,
        instructions=root / instruction_name,
        skill=root / "skills" / "evergreen",
        ownership=root / OWNERSHIP_FILE,
        problem=problem,
    )


def _select(home, requested):
    home = Path(home).expanduser()
    if _kind(home) != "directory":
        return [], f"home must be a real directory: {home}"
    home = home.resolve()
    statuses = detect_hosts(home)
    if requested == "all":
        selected = [status for status in statuses if status.present]
        return (selected, None) if selected else ([], "no supported host detected")
    selected = next((status for status in statuses if status.name == requested), None)
    if selected is None:
        return [], f"unsupported host: {requested}"
    return ([selected], None) if selected.present else ([], f"{requested} host not detected")


def _host_errors(selected):
    errors = []
    for status in selected:
        if status.problem:
            errors.append(f"{status.name}: refusing unsafe {status.problem}")
            continue
        skills = status.skill.parent
        skills_kind = _kind(skills)
        if skills_kind not in ("absent", "directory"):
            errors.append(f"{status.name}: refusing unsafe skills parent ({skills_kind})")
        instructions_kind = _kind(status.instructions)
        if instructions_kind not in ("absent", "regular"):
            errors.append(f"{status.name}: refusing unsafe instructions ({instructions_kind})")
        elif instructions_kind == "regular" and status.instructions.lstat().st_nlink != 1:
            errors.append(f"{status.name}: refusing instruction hard link")
        elif (instructions_kind == "regular" and
              status.instructions.lstat().st_size > MAX_INSTRUCTION_BYTES):
            errors.append(
                f"{status.name}: refusing instruction byte limit "
                f"(maximum {MAX_INSTRUCTION_BYTES})"
            )
        ownership_kind = _kind(status.ownership)
        if ownership_kind not in ("absent", "regular"):
            errors.append(f"{status.name}: refusing unsafe ownership record ({ownership_kind})")
        elif ownership_kind == "regular" and status.ownership.lstat().st_nlink != 1:
            errors.append(f"{status.name}: refusing ownership hard link")
    return errors


def _load_ownership(status):
    try:
        snapshot = _snapshot(status.ownership)
    except (OSError, ValueError) as error:
        return None, f"invalid ownership record: {error}"
    return _ownership_from_snapshot(status, snapshot)


def _ownership_from_snapshot(status, snapshot):
    if snapshot.kind == "absent":
        return None, None
    try:
        if snapshot.kind != "regular" or snapshot.data is None:
            raise ValueError(f"ownership record is {snapshot.kind}, not regular")
        value = json.loads(snapshot.data)
        fields = {
            "schema_version", "host", "plugin_root", "instruction_existed",
            "instruction_separator", "skill_target",
        }
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("ownership record has invalid fields")
        if value["schema_version"] != 1 or value["host"] != status.name:
            raise ValueError("ownership record identity mismatch")
        if type(value["instruction_existed"]) is not bool:
            raise ValueError("ownership record instruction_existed must be boolean")
        if type(value["instruction_separator"]) is not bool:
            raise ValueError("ownership record instruction_separator must be boolean")
        for field in ("plugin_root", "skill_target"):
            if (not isinstance(value[field], str) or not value[field] or
                    len(value[field].encode("utf-8")) > 2048):
                raise ValueError(f"ownership record {field} is invalid")
        return Ownership(**value), None
    except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        return None, f"invalid ownership record: {error}"


def _install_plans(status, state, root, target, block, captured):
    errors = []
    instruction = captured[status.instructions]
    skill = captured[status.skill]
    instruction_state, owned = _instruction_state_from_snapshot(instruction)
    skill_kind = skill.kind
    if state is None:
        if instruction_state == "owned":
            errors.append("owned markers lack an ownership record")
        elif instruction_state == "malformed":
            errors.append("ambiguous instruction markers")
        if skill_kind != "absent":
            errors.append(f"skill path lacks ownership proof ({skill_kind})")
        if errors:
            return [], errors
        original = instruction.data if instruction_state == "unowned" else b""
        separator = bool(original)
        state = Ownership(
            schema_version=1,
            host=status.name,
            plugin_root=str(root),
            instruction_existed=instruction_state == "unowned",
            instruction_separator=separator,
            skill_target=str(target),
        )
        instruction_value = original + (b"\n" if separator else b"") + block
        instruction_detail = "create" if instruction_state == "missing" else "append"
        instruction_plan = ("write", f"{instruction_detail} owned instructions {status.instructions}",
                            status.instructions, instruction_value)
        skill_plan = ("link", f"create skill link {status.skill}", status.skill, target)
    else:
        if instruction_state != "owned":
            errors.append("ownership record does not match instructions")
        if skill_kind not in ("absent", "symlink"):
            errors.append(f"owned skill path became {skill_kind}")
        if errors:
            return [], errors
        instruction_value = instruction.data.replace(owned, block, 1)
        instruction_plan = (
            "unchanged" if owned == block else "write",
            f"owned instructions {'unchanged' if owned == block else 'updated'} {status.instructions}",
            status.instructions,
            instruction_value,
        )
        if (skill_kind == "symlink" and
                _normalized_snapshot_target(skill) == _normalized_lexical_path(target)):
            skill_plan = ("unchanged", f"skill link unchanged {status.skill}", status.skill, None)
        else:
            skill_plan = ("link", f"repair skill link {status.skill}", status.skill, target)
        state = Ownership(
            schema_version=1,
            host=status.name,
            plugin_root=str(root),
            instruction_existed=state.instruction_existed,
            instruction_separator=state.instruction_separator,
            skill_target=str(target),
        )
    encoded = _encode_ownership(state)
    ownership = captured[status.ownership]
    current = ownership.data if ownership.kind == "regular" else None
    state_plan = (
        "unchanged" if current == encoded else "write",
        f"ownership record {'unchanged' if current == encoded else 'updated'} {status.ownership}",
        status.ownership,
        encoded,
    )
    return [instruction_plan, skill_plan, state_plan], []


def _uninstall_plans(status, state, captured):
    instruction = captured[status.instructions]
    skill = captured[status.skill]
    instruction_state, owned = _instruction_state_from_snapshot(instruction)
    skill_kind = skill.kind
    if state is None:
        errors = []
        if instruction_state in ("owned", "malformed"):
            errors.append("instruction markers lack ownership proof")
        if skill_kind == "symlink":
            errors.append("skill link lacks ownership proof")
        if errors:
            return [], errors
        return [
            ("unowned", f"leave unowned instructions {status.instructions}",
             status.instructions, None),
            ("unowned", f"leave unowned skill path {status.skill}", status.skill, None),
        ], []
    if instruction_state != "owned":
        return [], ["ownership record does not match instructions"]
    expected = _block(Path(state.plugin_root))
    if owned != expected:
        return [], ["owned instruction block was modified"]
    if skill_kind not in ("absent", "symlink"):
        return [], [f"owned skill path became {skill_kind}"]
    owns_skill_link = (
        skill_kind == "symlink" and
        _normalized_snapshot_target(skill) == _normalized_lexical_path(Path(state.skill_target))
    )
    if skill_kind == "symlink" and not owns_skill_link:
        return [], ["replacement skill link does not match ownership record"]

    content = instruction.data
    begin = content.index(BEGIN_MARKER.encode("ascii"))
    remove_from = begin
    if state.instruction_separator:
        if begin == 0 or content[begin - 1:begin] != b"\n":
            return [], ["owned instruction separator was modified"]
        remove_from -= 1
    restored = content[:remove_from] + content[begin + len(owned):]
    if restored or state.instruction_existed:
        instruction_plan = (
            "write", f"remove owned instructions from {status.instructions}",
            status.instructions, restored,
        )
    else:
        instruction_plan = (
            "delete", f"remove owned instructions file {status.instructions}",
            status.instructions, None,
        )
    if owns_skill_link:
        skill_plan = ("delete", f"remove owned skill link {status.skill}", status.skill, None)
    else:
        skill_plan = (
            "unchanged", f"owned skill link absent {status.skill}", status.skill, None,
        )
    state_plan = (
        "delete", f"remove ownership record {status.ownership}", status.ownership, None,
    )
    return [instruction_plan, skill_plan, state_plan], []


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
                        expected=captured[path],
                        parent_snapshot=captured[path.parent],
                        rollback_entries=rollback_entries,
                        conflicts=conflicts,
                    )
                except Exception:
                    try:
                        current = _snapshot_at(path, parent_fd)
                    except Exception as snapshot_error:
                        conflicts.append(
                            f"{path}: preserved concurrent state; postimage unavailable: "
                            f"{snapshot_error}"
                        )
                        raise
                    if current == captured[path]:
                        pass
                    elif _matches_postimage(action, value, current, captured[path]):
                        if not _entry_for_path(rollback_entries, path):
                            rollback_entries.append(RollbackEntry(
                                captured[path], current, captured[path.parent]
                            ))
                    else:
                        conflicts.append(f"{path}: preserved concurrent state")
                    raise
                try:
                    current = _snapshot_at(path, parent_fd)
                except Exception as snapshot_error:
                    conflicts.append(
                        f"{path}: preserved concurrent state; postimage unavailable: "
                        f"{snapshot_error}"
                    )
                    raise
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
    for entry in rollback_entries:
        if entry.backup is None:
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
    for snapshot in captured.values():
        _verify_snapshot(snapshot)


def _verify_snapshot(expected):
    actual = _snapshot(expected.path, allow_directory=expected.kind == "directory")
    if actual != expected:
        raise OSError(f"transaction path changed after planning: {expected.path}")


def _snapshot(path, allow_directory=False):
    kind = _kind(path)
    if kind == "absent":
        return PathSnapshot(path, kind)
    if kind == "regular":
        before = path.lstat()
        if before.st_nlink != 1:
            raise OSError(f"refusing hard-linked transaction path: {path}")
        limit = MAX_STATE_BYTES if path.name == OWNERSHIP_FILE else MAX_INSTRUCTION_BYTES
        data = _read_regular_bounded(path, limit, "transaction snapshot")
        metadata = path.lstat()
        if (before.st_dev, before.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise OSError(f"refusing changed transaction path: {path}")
        return PathSnapshot(
            path, kind, data=data, mode=stat.S_IMODE(metadata.st_mode),
            dev=metadata.st_dev, ino=metadata.st_ino, nlink=metadata.st_nlink,
            uid=metadata.st_uid, gid=metadata.st_gid,
            atime_ns=before.st_atime_ns, mtime_ns=before.st_mtime_ns,
        )
    if kind == "symlink":
        before = path.lstat()
        target = os.readlink(path)
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_mode, before.st_nlink) != (
                after.st_dev, after.st_ino, after.st_mode, after.st_nlink):
            raise OSError(f"refusing changed transaction symlink: {path}")
        return PathSnapshot(
            path, kind, target=target, mode=stat.S_IMODE(after.st_mode),
            dev=after.st_dev, ino=after.st_ino, nlink=after.st_nlink,
            uid=after.st_uid, gid=after.st_gid,
            atime_ns=before.st_atime_ns, mtime_ns=before.st_mtime_ns,
        )
    if kind == "directory" and allow_directory:
        metadata = path.lstat()
        return PathSnapshot(
            path, kind, mode=stat.S_IMODE(metadata.st_mode),
            dev=metadata.st_dev, ino=metadata.st_ino, nlink=metadata.st_nlink,
            uid=metadata.st_uid, gid=metadata.st_gid,
            atime_ns=metadata.st_atime_ns, mtime_ns=metadata.st_mtime_ns,
        )
    raise OSError(f"refusing unsafe transaction path ({kind}): {path}")


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
        os.mkdir(path.name, mode=0o755, dir_fd=descriptor)
        created = _snapshot_at(path, descriptor)
        if created.kind != "directory":
            raise OSError(f"transaction directory postimage mismatch: {path}")
        rollback_entries.append(RollbackEntry(snapshot, created, grandparent))
        os.fsync(descriptor)
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
        limit = MAX_STATE_BYTES if path.name == OWNERSHIP_FILE else MAX_INSTRUCTION_BYTES
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
            after_open = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        after_path = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_size,
            item.st_mtime_ns, item.st_ctime_ns,
        )
        if identity(metadata) != identity(after_open) or identity(metadata) != identity(after_path):
            raise OSError(f"transaction path changed: {path}")
        return PathSnapshot(path, kind, data=data, **common)
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
        if expected.kind == "absent":
            mode = 0o600 if path.name == OWNERSHIP_FILE else 0o644
            _create_write_at(parent_fd, path.name, value, mode)
        else:
            return _replace_regular_at(
                parent_fd, path, value, expected, parent_snapshot,
                rollback_entries, conflicts,
            )
    elif action == "link":
        if expected.kind == "absent":
            os.symlink(value, path.name, target_is_directory=True, dir_fd=parent_fd)
            os.fsync(parent_fd)
        else:
            _atomic_link_at(parent_fd, path.name, value, expected)
    elif action == "delete":
        if expected.kind == "absent":
            return expected
        if expected.kind == "regular":
            return _delete_regular_at(
                parent_fd, path, expected, parent_snapshot,
                rollback_entries, conflicts,
            )
        try:
            os.unlink(path.name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        os.fsync(parent_fd)
    postimage = _snapshot_at(path, parent_fd)
    if not _matches_postimage(action, value, postimage, expected):
        raise OSError(f"transaction postimage mismatch: {path}")
    return postimage


def _replace_regular_at(
    parent_fd, path, data, expected, parent_snapshot, rollback_entries, conflicts,
):
    temporary = f".{path.name}.evergreen-{uuid.uuid4().hex}"
    backup = f".{path.name}.evergreen-backup-{uuid.uuid4().hex}"
    temp_fd = None
    source_fd = None
    backup_created = False
    published = False
    entry = None
    try:
        temp_fd = os.open(
            temporary, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600,
            dir_fd=parent_fd,
        )
        _replace_descriptor_bytes(temp_fd, data, path)
        source_fd = os.open(
            path.name,
            os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
            getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_fd,
        )
        _verify_snapshot_at(expected, parent_fd)
        source = os.fstat(source_fd)
        current = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
        if (_metadata_identity(source) != _snapshot_identity(expected) or
                _metadata_identity(current) != _snapshot_identity(expected)):
            raise OSError(f"transaction path changed before in-place write: {expected.path}")
        os.utime(source_fd, ns=(expected.atime_ns, expected.mtime_ns))
        source = os.fstat(source_fd)
        _clone_regular_metadata(source_fd, temp_fd, source, expected)
        temp_metadata = os.fstat(temp_fd)
        postimage = PathSnapshot(
            path, "regular", data=data, mode=stat.S_IMODE(temp_metadata.st_mode),
            dev=temp_metadata.st_dev, ino=temp_metadata.st_ino,
            nlink=temp_metadata.st_nlink, uid=temp_metadata.st_uid,
            gid=temp_metadata.st_gid, atime_ns=expected.atime_ns,
            mtime_ns=expected.mtime_ns,
        )
        if not _matches_postimage("write", data, postimage, expected):
            raise OSError(f"transaction temporary metadata mismatch: {path}")
        os.link(
            path.name, backup, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        backup_created = True
        os.fsync(parent_fd)
        entry = RollbackEntry(expected, postimage, parent_snapshot, backup)
        rollback_entries.append(entry)
        try:
            os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            published = True
        except BaseException:
            rollback_entries.remove(entry)
            entry = None
            raise
        _verify_snapshot_at(postimage, parent_fd)
        os.fsync(parent_fd)
        return postimage
    finally:
        close_errors = []
        for descriptor, label in ((source_fd, "source"), (temp_fd, "temporary")):
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except OSError as error:
                close_errors.append(f"{label} descriptor: {error}")
        _cleanup_artifact(parent_fd, temporary, path, "temporary", conflicts)
        if backup_created and not published:
            _cleanup_artifact(parent_fd, backup, path, "backup", conflicts)
        if close_errors:
            conflicts.append(
                f"{path}: manual recovery required after descriptor cleanup failure: "
                + "; ".join(close_errors)
            )
            raise OSError(f"transaction descriptor cleanup failed: {path}")


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
    return (
        snapshot.kind, snapshot.dev, snapshot.ino, snapshot.mode, snapshot.nlink,
        snapshot.uid, snapshot.gid,
    )


def _clone_regular_metadata(source_fd, destination_fd, source, expected):
    native_metadata = (
        _posix is not None and hasattr(_posix, "_fcopyfile") and
        all(hasattr(_posix, name) for name in (
            "_COPYFILE_STAT", "_COPYFILE_ACL", "_COPYFILE_XATTR",
        ))
    )
    if native_metadata:
        _posix._fcopyfile(
            source_fd,
            destination_fd,
            _posix._COPYFILE_STAT | _posix._COPYFILE_ACL | _posix._COPYFILE_XATTR,
        )
    destination = os.fstat(destination_fd)
    if (destination.st_uid, destination.st_gid) != (source.st_uid, source.st_gid):
        os.fchown(destination_fd, source.st_uid, source.st_gid)
    if not native_metadata:
        _clone_xattrs(source_fd, destination_fd)
    os.fchmod(destination_fd, stat.S_IMODE(source.st_mode))
    os.utime(destination_fd, ns=(expected.atime_ns, expected.mtime_ns))
    os.fsync(destination_fd)


def _clone_xattrs(source_fd, destination_fd):
    if not all(hasattr(os, name) for name in ("listxattr", "getxattr", "setxattr")):
        raise OSError("extended-attribute APIs are unavailable")
    unsupported = {
        errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA),
        errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }
    try:
        names = set(os.listxattr(source_fd))
    except OSError as error:
        if error.errno in unsupported:
            names = set()
        else:
            raise
    acl_name = "com.apple.acl.text"
    if acl_name not in names:
        try:
            os.getxattr(source_fd, acl_name)
            names.add(acl_name)
        except OSError as error:
            if error.errno not in unsupported:
                raise
    if len(names) > MAX_XATTRS:
        raise OSError(f"file has more than {MAX_XATTRS} extended attributes")
    used = 0
    for name in sorted(names):
        value = os.getxattr(source_fd, name)
        used += len(name.encode("utf-8")) + len(value)
        if used > MAX_XATTR_BYTES:
            raise OSError(f"extended attributes exceed {MAX_XATTR_BYTES} bytes")
        os.setxattr(destination_fd, name, value)


def _delete_regular_at(
    parent_fd, path, expected, parent_snapshot, rollback_entries, conflicts,
):
    backup = f".{path.name}.evergreen-backup-{uuid.uuid4().hex}"
    backup_created = False
    deleted = False
    entry = RollbackEntry(
        expected, PathSnapshot(path, "absent"), parent_snapshot, backup
    )
    try:
        _verify_snapshot_at(expected, parent_fd)
        os.link(
            path.name, backup, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        backup_created = True
        os.fsync(parent_fd)
        rollback_entries.append(entry)
        try:
            os.unlink(path.name, dir_fd=parent_fd)
            deleted = True
        except BaseException:
            rollback_entries.remove(entry)
            raise
        os.fsync(parent_fd)
        return entry.after
    finally:
        if backup_created and not deleted:
            _cleanup_artifact(parent_fd, backup, path, "backup", conflicts)


def _cleanup_artifact(parent_fd, name, path, label, conflicts):
    try:
        os.unlink(name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except FileNotFoundError:
        return
    except OSError as error:
        conflicts.append(
            f"{path}: manual recovery required; {label} retained at "
            f"{path.parent / name}: {error}"
        )


def _create_write_at(parent_fd, name, data, mode):
    temporary = f".{name}.evergreen-{uuid.uuid4().hex}"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode, dir_fd=parent_fd,
    )
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = -1
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(
            temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        os.unlink(temporary, dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _atomic_link_at(parent_fd, name, target, expected):
    temporary = f".{name}.evergreen-{uuid.uuid4().hex}"
    try:
        os.symlink(target, temporary, target_is_directory=True, dir_fd=parent_fd)
        _verify_snapshot_at(expected, parent_fd)
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        os.fsync(parent_fd)
    finally:
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except FileNotFoundError:
            pass


def _restore_entry(entry):
    parent_fd = _open_directory(entry.parent)
    try:
        actual = _snapshot_at(entry.after.path, parent_fd)
        if actual != entry.after:
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
            except OSError as error:
                raise OSError(
                    f"backup restore failed; backup retained at {_backup_path(entry)}: "
                    f"{error}"
                ) from error
            try:
                os.fsync(parent_fd)
            except OSError as error:
                raise OSError(
                    f"backup restored to {entry.before.path}, but directory durability "
                    f"failed; inspect former backup path {_backup_path(entry)}: {error}"
                ) from error
            return
        before = entry.before
        if before.kind == "absent":
            if entry.after.kind == "directory":
                try:
                    _verify_snapshot_at(entry.after, parent_fd)
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
        try:
            os.unlink(entry.backup, dir_fd=parent_fd)
            os.fsync(parent_fd)
        except OSError as error:
            raise OSError(
                f"backup cleanup failed at {_backup_path(entry)}: {error}"
            ) from error
    finally:
        os.close(parent_fd)


def _verify_backup(parent_fd, entry):
    if entry.backup is None:
        raise OSError("transaction backup is missing")
    try:
        metadata = os.stat(entry.backup, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise OSError(f"transaction backup unavailable at {_backup_path(entry)}: {error}") from error
    if _metadata_identity(metadata) != _snapshot_identity(entry.before):
        raise OSError(f"transaction backup changed at {_backup_path(entry)}")


def _backup_path(entry):
    return entry.before.path.parent / entry.backup


def _instruction_state(path):
    if _kind(path) == "absent":
        return "missing", None
    if _kind(path) != "regular":
        return "malformed", None
    try:
        snapshot = _snapshot(path)
    except (OSError, ValueError):
        return "malformed", None
    return _instruction_state_from_snapshot(snapshot)


def _instruction_state_from_snapshot(snapshot):
    if snapshot.kind == "absent":
        return "missing", None
    if snapshot.kind != "regular" or snapshot.data is None:
        return "malformed", None
    content = snapshot.data
    begin_marker = BEGIN_MARKER.encode("ascii")
    end_marker = END_MARKER.encode("ascii")
    if content.count(begin_marker) == content.count(end_marker) == 0:
        return "unowned", None
    if content.count(begin_marker) != 1 or content.count(end_marker) != 1:
        return "malformed", None
    begin = content.index(begin_marker)
    marker_end = content.find(end_marker, begin + len(begin_marker))
    if marker_end < 0:
        return "malformed", None
    end = marker_end + len(end_marker)
    if content[end:end + 1] == b"\n":
        end += 1
    return "owned", content[begin:end]


def _block(plugin_root):
    rule_path = json.dumps(str(plugin_root / "AGENTS.md"), ensure_ascii=True)
    return (
        f"{BEGIN_MARKER}\n"
        f"Evergreen canonical rules: read and follow {rule_path} on every response.\n"
        f"{END_MARKER}\n"
    ).encode("ascii")


def _encode_ownership(value):
    encoded = (json.dumps(asdict(value), sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(encoded) > MAX_STATE_BYTES:
        raise ValueError("ownership record exceeds byte limit")
    return encoded


def _absolute(path):
    expanded = Path(os.path.expanduser(str(path)))
    absolute = expanded if expanded.is_absolute() else Path.cwd() / expanded
    return absolute.resolve(strict=False)


def _normalized_lexical_path(path):
    return Path(os.path.normpath(os.path.abspath(os.path.expanduser(str(path)))))


def _normalized_link_target(path):
    target = Path(os.readlink(path))
    return _normalized_lexical_path(target if target.is_absolute() else path.parent / target)


def _normalized_snapshot_target(snapshot):
    if snapshot.kind != "symlink" or snapshot.target is None:
        return None
    target = Path(snapshot.target)
    return _normalized_lexical_path(
        target if target.is_absolute() else snapshot.path.parent / target
    )


def _read_instruction(path):
    return _read_regular_bounded(path, MAX_INSTRUCTION_BYTES, "instruction file")


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
    if time.monotonic() > deadline:
        raise TimeoutError(f"{label} read exceeded elapsed-time limit")
    flags = os.O_RDONLY | nonblocking | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        if time.monotonic() > deadline:
            raise TimeoutError(f"{label} read exceeded elapsed-time limit")
        opened = os.fstat(descriptor)
        if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1):
            raise OSError(f"refusing changed {label}: {path}")
        if opened.st_size > limit:
            raise ValueError(f"{label} exceeds byte limit (maximum {limit})")
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
        if len(data) > limit:
            raise ValueError(f"{label} exceeds byte limit (maximum {limit})")
        if time.monotonic() > deadline:
            raise TimeoutError(f"{label} read exceeded elapsed-time limit")
        after = path.lstat()
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_size,
            item.st_mtime_ns, item.st_ctime_ns,
        )
        if identity(after) != identity(opened) or identity(before) != identity(opened):
            raise OSError(f"refusing changed {label}: {path}")
        return data
    finally:
        os.close(descriptor)


def _validate_python_command(command):
    try:
        payload = _read_regular_bounded(command, MAX_COMMAND_BYTES, "canonical command")
        source = payload.decode("utf-8")
        first_line = source.splitlines()[0] if source else ""
        if not _is_python_shebang(first_line):
            return "canonical command requires a Python shebang"
        ast.parse(source, filename=str(command))
    except UnicodeDecodeError as error:
        return f"canonical command is not UTF-8: {error}"
    except (OSError, SyntaxError, ValueError) as error:
        return str(error)
    return None


def _is_python_shebang(line):
    if not line.startswith("#!"):
        return False
    fields = line[2:].strip().split()
    if len(fields) == 1:
        interpreter = fields[0]
        return interpreter.startswith("/") and _is_python_name(Path(interpreter).name)
    return (
        len(fields) == 2 and fields[0] == "/usr/bin/env" and
        _is_python_name(fields[1])
    )


def _is_python_name(name):
    if name in ("python", "python3"):
        return True
    minor = name.removeprefix("python3.") if name.startswith("python3.") else ""
    return (
        1 <= len(minor) <= 3 and minor.isdigit() and
        (minor == "0" or not minor.startswith("0"))
    )


def _kind(path):
    try:
        mode = Path(path).lstat().st_mode
    except FileNotFoundError:
        return "absent"
    if stat.S_ISLNK(mode):
        return "symlink"
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISREG(mode):
        return "regular"
    return "other"
