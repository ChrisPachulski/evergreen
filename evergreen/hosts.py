"""Reversible Claude and Codex host integration."""

from dataclasses import dataclass
import json
import os
from pathlib import Path


BEGIN_MARKER = "<!-- evergreen:begin -->"
END_MARKER = "<!-- evergreen:end -->"


@dataclass(frozen=True)
class HostStatus:
    name: str
    present: bool
    root: Path
    instructions: Path
    skill: Path


@dataclass(frozen=True)
class OperationResult:
    ok: bool
    messages: tuple[str, ...]


def detect_hosts(home: Path) -> list[HostStatus]:
    home = Path(home)
    return [
        _status(home, "claude", ".claude", "CLAUDE.md"),
        _status(home, "codex", ".codex", "AGENTS.md"),
    ]


def install(home: Path, plugin_root: Path, host: str, dry_run: bool = False) -> OperationResult:
    selected, error = _select(home, host)
    if error:
        return OperationResult(False, (error,))
    target = (Path(plugin_root) / "skills" / "evergreen").resolve()
    block = _block(Path(plugin_root).resolve())
    plans = []
    errors = []
    for status in selected:
        instruction_plan = _plan_instruction_install(status.instructions, block)
        skill_plan = _plan_skill_install(status.skill, target)
        for plan in (instruction_plan, skill_plan):
            if plan[0] == "conflict":
                errors.append(f"{status.name}: refusing {plan[1]}")
            else:
                plans.append((status, plan))
    if errors:
        return OperationResult(False, tuple(errors))

    messages = []
    for status, plan in plans:
        action, detail, path, value = plan
        prefix = "would " if dry_run and action != "unchanged" else ""
        messages.append(f"{status.name}: {prefix}{detail}")
        if dry_run or action == "unchanged":
            continue
        if action == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value)
        elif action == "link":
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.is_symlink():
                path.unlink()
            path.symlink_to(value, target_is_directory=True)
    return OperationResult(True, tuple(messages))


def uninstall(home: Path, host: str, dry_run: bool = False) -> OperationResult:
    selected, error = _select(home, host)
    if error:
        return OperationResult(False, (error,))
    plans = []
    errors = []
    for status in selected:
        instruction_plan = _plan_instruction_uninstall(status.instructions)
        skill_plan = _plan_skill_uninstall(status.skill)
        for plan in (instruction_plan, skill_plan):
            if plan[0] == "conflict":
                errors.append(f"{status.name}: refusing {plan[1]}")
            else:
                plans.append((status, plan))
    if errors:
        return OperationResult(False, tuple(errors))

    messages = []
    for status, plan in plans:
        action, detail, path, value = plan
        prefix = "would " if dry_run and action not in ("unchanged", "unowned") else ""
        messages.append(f"{status.name}: {prefix}{detail}")
        if dry_run or action in ("unchanged", "unowned"):
            continue
        if action == "write":
            path.write_text(value)
        elif action == "delete":
            path.unlink()
    return OperationResult(True, tuple(messages))


def doctor(home: Path, plugin_root: Path, host: str = "all") -> OperationResult:
    root = Path(plugin_root).resolve()
    messages = []
    healthy = True

    try:
        claude = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
        codex = json.loads((root / ".codex-plugin" / "plugin.json").read_text())
        if not isinstance(claude, dict) or not isinstance(codex, dict):
            raise ValueError("manifest root must be an object")
        version = claude.get("version")
        if not isinstance(version, str) or not version or codex.get("version") != version:
            raise ValueError("manifest versions differ")
        messages.append(f"ok canonical version {version}; manifests agree")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        healthy = False
        messages.append(f"error manifests: {error}")

    if (root / "AGENTS.md").is_file() and (root / "skills" / "evergreen" / "SKILL.md").is_file():
        messages.append("ok canonical rules")
    else:
        healthy = False
        messages.append("error canonical rules missing")
    command = root / "bin" / "evergreen"
    if command.is_file() and os.access(command, os.X_OK):
        messages.append("ok command available")
    else:
        healthy = False
        messages.append("error command unavailable")

    selected, error = _select(home, host)
    if error:
        return OperationResult(False, tuple(messages + [error]))
    expected_block = _block(root)
    expected_skill = (root / "skills" / "evergreen").resolve()
    for status in selected:
        state, owned = _instruction_state(status.instructions)
        if state == "owned" and owned == expected_block:
            messages.append(f"ok {status.name} owned instructions")
        elif state == "owned":
            healthy = False
            messages.append(f"error {status.name} stale owned instructions")
        else:
            healthy = False
            messages.append(f"error {status.name} {state} instructions")

        if status.skill.is_symlink():
            if not status.skill.exists():
                healthy = False
                messages.append(f"error {status.name} broken skill link")
            elif status.skill.resolve() != expected_skill:
                healthy = False
                messages.append(f"error {status.name} stale skill link")
            else:
                messages.append(f"ok {status.name} skill link")
        elif status.skill.exists():
            healthy = False
            messages.append(f"error {status.name} unowned skill path")
        else:
            healthy = False
            messages.append(f"error {status.name} missing skill link")
    return OperationResult(healthy, tuple(messages))


def _status(home, name, directory, instruction_name):
    root = home / directory
    return HostStatus(
        name=name,
        present=root.is_dir(),
        root=root,
        instructions=root / instruction_name,
        skill=root / "skills" / "evergreen",
    )


def _select(home, requested):
    statuses = detect_hosts(Path(home))
    if requested == "all":
        selected = [status for status in statuses if status.present]
        if not selected:
            return [], "no supported host detected"
        return selected, None
    selected = next(status for status in statuses if status.name == requested)
    if not selected.present:
        return [], f"{requested} host not detected"
    return [selected], None


def _block(plugin_root):
    rule_path = json.dumps(str(plugin_root / "AGENTS.md"), ensure_ascii=True)
    return (
        f"{BEGIN_MARKER}\n"
        f"Evergreen canonical rules: read and follow {rule_path} on every response.\n"
        f"{END_MARKER}\n"
    )


def _instruction_state(path):
    if path.is_symlink():
        return "malformed", None
    if not path.exists():
        return "missing", None
    if not path.is_file():
        return "malformed", None
    text = path.read_text()
    begin_count = text.count(BEGIN_MARKER)
    end_count = text.count(END_MARKER)
    if begin_count == end_count == 0:
        return "unowned", None
    if begin_count != 1 or end_count != 1:
        return "malformed", None
    begin = text.index(BEGIN_MARKER)
    marker_end = text.find(END_MARKER, begin + len(BEGIN_MARKER))
    if marker_end < 0:
        return "malformed", None
    end = marker_end + len(END_MARKER)
    if end < len(text) and text[end] == "\n":
        end += 1
    return "owned", text[begin:end]


def _plan_instruction_install(path, block):
    state, owned = _instruction_state(path)
    if state == "malformed":
        return "conflict", f"ambiguous markers in {path}", path, None
    if state == "missing":
        return "write", f"create owned instructions {path}", path, block
    text = path.read_text()
    if state == "unowned":
        separator = "\n" if text else ""
        return "write", f"append owned instructions {path}", path, text + separator + block
    if owned == block:
        return "unchanged", f"owned instructions unchanged {path}", path, None
    return "write", f"update owned instructions {path}", path, text.replace(owned, block, 1)


def _plan_skill_install(path, target):
    if path.is_symlink():
        if path.exists() and path.resolve() == target:
            return "unchanged", f"skill link unchanged {path}", path, None
        return "link", f"repair skill link {path}", path, target
    if path.exists():
        return "conflict", f"unowned skill path {path}", path, None
    return "link", f"create skill link {path}", path, target


def _plan_instruction_uninstall(path):
    state, owned = _instruction_state(path)
    if state == "malformed":
        return "conflict", f"ambiguous markers in {path}", path, None
    if state in ("missing", "unowned"):
        return "unowned", f"leave unowned instructions {path}", path, None
    text = path.read_text()
    begin = text.index(BEGIN_MARKER)
    remove_from = begin - 1 if begin and text[begin - 1] == "\n" else begin
    text = text[:remove_from] + text[begin + len(owned):]
    if text:
        return "write", f"remove owned instructions from {path}", path, text
    return "delete", f"remove owned instructions file {path}", path, None


def _plan_skill_uninstall(path):
    if path.is_symlink():
        return "delete", f"remove owned skill link {path}", path, None
    if path.exists():
        return "unowned", f"leave unowned skill path {path}", path, None
    return "unchanged", f"skill link absent {path}", path, None
