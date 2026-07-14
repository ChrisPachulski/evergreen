"""Reversible Claude and Codex host integration."""

import ast
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path

from .host_lock import runtime_error as _runtime_error
from .host_metadata import native_copy_available as _native_copy_available
from .host_snapshot import (
    capture_authorization as _capture_authorization,
    capture_preflight as _capture_preflight, kind as _kind,
    read_regular_bounded as _read_regular_bounded,
    resolve_managed_root as _resolve_managed_root,
    snapshot as _snapshot,
)
from .host_transaction import TransactionEngine
from .host_types import HostStatus, OperationResult, Ownership

BEGIN_MARKER = "<!-- evergreen:begin -->"
END_MARKER = "<!-- evergreen:end -->"
OWNERSHIP_FILE = ".evergreen-owned.json"
MAX_STATE_BYTES = 4096
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTRUCTION_BYTES = 1024 * 1024
MAX_COMMAND_BYTES = 1024 * 1024


def _host_runtime_error():
    return _runtime_error(_native_copy_available)


def detect_hosts(home: Path) -> list[HostStatus]:
    home = Path(home)
    return [
        _status(home, "claude", ".claude", "CLAUDE.md"),
        _status(home, "codex", ".codex", "AGENTS.md"),
    ]


def install(home: Path, plugin_root: Path, host: str, dry_run: bool = False) -> OperationResult:
    runtime_error = _host_runtime_error()
    if runtime_error:
        return OperationResult(False, (runtime_error,))
    canonical, canonical_messages = _canonical(plugin_root)
    if canonical is None:
        return OperationResult(False, tuple(canonical_messages))
    root, target, _version = canonical
    selected, error = _select(home, host)
    if error:
        return OperationResult(False, (error,))
    authorization, authorization_error = _authorize_selection(selected)
    if authorization_error:
        return OperationResult(False, tuple(authorization_error))
    engine, lock_error = TransactionEngine.acquire(selected, authorization)
    if lock_error:
        return OperationResult(False, (lock_error,))
    return _with_engine_close(engine, lambda: _install_acquired(
        engine, selected, root, target, dry_run,
    ))


def _install_acquired(engine, selected, root, target, dry_run):
    try:
        try:
            recovery_errors = engine.recover()
        except Exception as error:
            return OperationResult(False, (f"error: transaction recovery failed: {error}",))
        if recovery_errors:
            return OperationResult(False, tuple(recovery_errors))
        errors = _host_errors(selected)
        if errors:
            return OperationResult(False, tuple(errors))
        return _install_locked(selected, root, target, dry_run, engine)
    except Exception as error:
        return OperationResult(False, (f"error: host operation failed: {error}",))


def _install_locked(selected, root, target, dry_run, engine):
    errors = []
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
        elif status.root != status.resolved_root and state is None:
            errors.append(f"{status.name}: managed host root lacks ownership proof")
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
    return engine.apply(plans, dry_run, captured)


def uninstall(home: Path, host: str, dry_run: bool = False) -> OperationResult:
    runtime_error = _host_runtime_error()
    if runtime_error:
        return OperationResult(False, (runtime_error,))
    selected, error = _select(home, host)
    if error:
        return OperationResult(False, (error,))
    authorization, authorization_error = _authorize_selection(selected)
    if authorization_error:
        return OperationResult(False, tuple(authorization_error))
    engine, lock_error = TransactionEngine.acquire(selected, authorization)
    if lock_error:
        return OperationResult(False, (lock_error,))
    return _with_engine_close(engine, lambda: _uninstall_acquired(
        engine, selected, dry_run,
    ))


def _uninstall_acquired(engine, selected, dry_run):
    try:
        try:
            recovery_errors = engine.recover()
        except Exception as error:
            return OperationResult(False, (f"error: transaction recovery failed: {error}",))
        if recovery_errors:
            return OperationResult(False, tuple(recovery_errors))
        errors = _host_errors(selected)
        if errors:
            return OperationResult(False, tuple(errors))
        return _uninstall_locked(selected, dry_run, engine)
    except Exception as error:
        return OperationResult(False, (f"error: host operation failed: {error}",))


def _with_engine_close(engine, operation):
    try:
        result = operation()
    except Exception as error:
        result = OperationResult(False, (f"error: host operation failed: {error}",))
    close_error = engine.close()
    if close_error:
        return OperationResult(False, result.messages + (close_error,))
    return result


def _uninstall_locked(selected, dry_run, engine):
    errors = []
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
        elif status.root != status.resolved_root and state is None:
            errors.append(f"{status.name}: managed host root lacks ownership proof")
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
    return engine.apply(plans, dry_run, captured)


def doctor(home: Path, plugin_root: Path, host: str = "all") -> OperationResult:
    canonical, messages = _canonical(plugin_root)
    healthy = canonical is not None
    selected, error = _select(home, host)
    if error:
        return OperationResult(False, tuple(messages + [error]))
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
        host_errors = _host_errors([status])
        if host_errors:
            healthy = False
            messages.extend(host_errors)
            continue
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
        (root / "AGENTS.md", MAX_INSTRUCTION_BYTES),
        (root / "skills" / "evergreen" / "SKILL.md", MAX_INSTRUCTION_BYTES),
        (root / "bin" / "evergreen", MAX_COMMAND_BYTES),
    )
    for path, _limit in required_files:
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
    hashes = {}
    if not errors:
        for path, limit in required_files:
            try:
                hashes[path.relative_to(root).as_posix()] = hashlib.sha256(
                    _read_regular_bounded(path, limit, "canonical file")
                ).hexdigest()
            except (OSError, ValueError) as error:
                errors.append(f"error canonical file {path}: {error}")
    if errors:
        return None, errors
    version = versions[0]
    return (root, root / "skills" / "evergreen", version), [
        f"ok canonical version {version}; manifests agree",
        "ok canonical hashes " + " ".join(
            f"{name}={digest}" for name, digest in hashes.items()
        ),
        "ok canonical rules",
        "ok command available",
    ]


def _status(home, name, directory, instruction_name):
    root = home / directory
    root_kind = _kind(root)
    resolved_root = root
    problem = None
    managed_chain = ()
    if root_kind == "symlink":
        resolved_root, managed_chain, problem = _resolve_managed_root(home, root)
    elif root_kind not in ("absent", "directory"):
        problem = f"host root is {root_kind}, not a directory"
    return HostStatus(
        name=name,
        present=root_kind != "absent",
        root=root,
        resolved_root=resolved_root,
        instructions=resolved_root / instruction_name,
        skill=resolved_root / "skills" / "evergreen",
        ownership=resolved_root / OWNERSHIP_FILE,
        problem=problem,
        managed_chain=managed_chain,
    )

def _authorize_selection(selected):
    try:
        captured = _capture_authorization(selected)
    except Exception as error:
        return None, [f"error: host authorization failed: {error}"]
    errors = []
    for status in selected:
        if status.root == status.resolved_root:
            continue
        state, state_error = _ownership_from_snapshot(status, captured[status.ownership])
        if state_error:
            errors.append(f"{status.name}: {state_error}")
        elif state is None:
            errors.append(f"{status.name}: managed host root lacks ownership proof")
    return (captured, None) if not errors else (None, errors)


def _select(home, requested):
    home = _normalized_lexical_path(Path(home).expanduser())
    if _kind(home) != "directory":
        return [], f"home must be a real directory: {home}"
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


def _validate_python_command(command):
    try:
        payload = _read_regular_bounded(command, MAX_COMMAND_BYTES, "canonical command")
        source = payload.decode("utf-8")
        first_line = source.partition("\n")[0] if source else ""
        if not _is_python_shebang(first_line):
            return "canonical command requires a Python shebang"
        ast.parse(source, filename=str(command))
    except UnicodeDecodeError as error:
        return f"canonical command is not UTF-8: {error}"
    except (OSError, SyntaxError, ValueError) as error:
        return str(error)
    return None


def _is_python_shebang(line):
    if not line.startswith("#!") or any(ord(char) < 32 or ord(char) == 127 for char in line):
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
