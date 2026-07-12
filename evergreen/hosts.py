"""Reversible Claude and Codex host integration."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import stat


BEGIN_MARKER = "<!-- evergreen:begin -->"
END_MARKER = "<!-- evergreen:end -->"
OWNERSHIP_FILE = ".evergreen-owned.json"
MAX_STATE_BYTES = 4096
MAX_MANIFEST_BYTES = 64 * 1024


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
    states = {}
    for status in selected:
        state, state_error = _load_ownership(status)
        states[status.name] = state
        if state_error:
            errors.append(f"{status.name}: {state_error}")
    if errors:
        return OperationResult(False, tuple(errors))

    block = _block(root)
    plans = []
    for status in selected:
        state = states[status.name]
        host_plans, plan_errors = _install_plans(status, state, root, target, block)
        plans.extend((status, plan) for plan in host_plans)
        errors.extend(f"{status.name}: refusing {message}" for message in plan_errors)
    if errors:
        return OperationResult(False, tuple(errors))
    return _apply(plans, dry_run)


def uninstall(home: Path, host: str, dry_run: bool = False) -> OperationResult:
    selected, error = _select(home, host)
    if error:
        return OperationResult(False, (error,))
    errors = _host_errors(selected)
    if errors:
        return OperationResult(False, tuple(errors))
    states = {}
    for status in selected:
        state, state_error = _load_ownership(status)
        states[status.name] = state
        if state_error:
            errors.append(f"{status.name}: {state_error}")
    if errors:
        return OperationResult(False, tuple(errors))

    plans = []
    for status in selected:
        host_plans, plan_errors = _uninstall_plans(status, states[status.name])
        plans.extend((status, plan) for plan in host_plans)
        errors.extend(f"{status.name}: refusing {message}" for message in plan_errors)
    if errors:
        return OperationResult(False, tuple(errors))
    return _apply(plans, dry_run)


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
    root = _absolute(plugin_root)
    errors = []
    if _kind(root) != "directory":
        return None, [f"error canonical plugin root must be a real directory: {root}"]
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
            if path.stat().st_size > MAX_MANIFEST_BYTES:
                raise ValueError("manifest exceeds byte limit")
            value = json.loads(path.read_bytes())
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
    home = Path(home)
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
        ownership_kind = _kind(status.ownership)
        if ownership_kind not in ("absent", "regular"):
            errors.append(f"{status.name}: refusing unsafe ownership record ({ownership_kind})")
    return errors


def _load_ownership(status):
    if _kind(status.ownership) == "absent":
        return None, None
    try:
        if status.ownership.stat().st_size > MAX_STATE_BYTES:
            raise ValueError("ownership record exceeds byte limit")
        value = json.loads(status.ownership.read_bytes())
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


def _install_plans(status, state, root, target, block):
    errors = []
    instruction_state, owned = _instruction_state(status.instructions)
    skill_kind = _kind(status.skill)
    if state is None:
        if instruction_state == "owned":
            errors.append("owned markers lack an ownership record")
        elif instruction_state == "malformed":
            errors.append("ambiguous instruction markers")
        if skill_kind != "absent":
            errors.append(f"skill path lacks ownership proof ({skill_kind})")
        if errors:
            return [], errors
        original = status.instructions.read_bytes() if instruction_state == "unowned" else b""
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
        instruction_value = status.instructions.read_bytes().replace(owned, block, 1)
        instruction_plan = (
            "unchanged" if owned == block else "write",
            f"owned instructions {'unchanged' if owned == block else 'updated'} {status.instructions}",
            status.instructions,
            instruction_value,
        )
        if skill_kind == "symlink" and status.skill.exists() and status.skill.resolve() == target:
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
    current = status.ownership.read_bytes() if _kind(status.ownership) == "regular" else None
    state_plan = (
        "unchanged" if current == encoded else "write",
        f"ownership record {'unchanged' if current == encoded else 'updated'} {status.ownership}",
        status.ownership,
        encoded,
    )
    return [instruction_plan, skill_plan, state_plan], []


def _uninstall_plans(status, state):
    instruction_state, owned = _instruction_state(status.instructions)
    skill_kind = _kind(status.skill)
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

    content = status.instructions.read_bytes()
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
    skill_plan = (
        "delete" if skill_kind == "symlink" else "unchanged",
        f"{'remove' if skill_kind == 'symlink' else 'owned skill link absent'} {status.skill}",
        status.skill,
        None,
    )
    state_plan = (
        "delete", f"remove ownership record {status.ownership}", status.ownership, None,
    )
    return [instruction_plan, skill_plan, state_plan], []


def _apply(plans, dry_run):
    messages = []
    for status, (action, detail, path, value) in plans:
        prefix = "would " if dry_run and action not in ("unchanged", "unowned") else ""
        messages.append(f"{status.name}: {prefix}{detail}")
        if dry_run or action in ("unchanged", "unowned"):
            continue
        if action == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        elif action == "link":
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            path.symlink_to(value, target_is_directory=True)
        elif action == "delete" and (path.is_symlink() or path.exists()):
            path.unlink()
    return OperationResult(True, tuple(messages))


def _instruction_state(path):
    if _kind(path) == "absent":
        return "missing", None
    if _kind(path) != "regular":
        return "malformed", None
    content = path.read_bytes()
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
    return Path(os.path.abspath(os.path.expanduser(str(path))))


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
