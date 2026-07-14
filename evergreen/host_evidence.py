"""Bounded, read-only Claude and Codex host evidence."""

import hashlib
import os
from pathlib import Path, PurePosixPath
import stat

from . import hosts as _hosts

OWNERSHIP_FILE = _hosts.OWNERSHIP_FILE
MAX_COMMAND_BYTES = _hosts.MAX_COMMAND_BYTES
MAX_EVIDENCE_FILES = 256
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024
PACKAGE_SOURCES = (
    "__init__.py", "evidence.py", "execution_policy.py", "grade.py",
    "host_commit.py", "host_evidence.py", "host_journal.py", "host_lock.py",
    "host_metadata.py", "host_snapshot.py", "host_transaction.py", "host_types.py",
    "hosts.py", "impact.py", "receipt.py",
)
HOST_EVIDENCE_FIELDS = {
    "lexical_root", "resolved_root", "resolution_chain", "ownership",
    "installed", "doctor_issues", "discovery", "uninstall_owned_paths",
}
INSTALLED_EVIDENCE_FIELDS = {
    "resolved_root", "instruction_state", "instruction_block_sha256",
    "artifacts", "skill_kind", "skill_target", "skill_hashes", "command_hashes",
    "manifest_sha256", "version",
}

_absolute = _hosts._absolute
_block = _hosts._block
_canonical = _hosts._canonical
_capture_preflight = _hosts._capture_preflight
_kind = _hosts._kind
_normalized_lexical_path = _hosts._normalized_lexical_path
_normalized_snapshot_target = _hosts._normalized_snapshot_target
_ownership_from_snapshot = _hosts._ownership_from_snapshot
_instruction_state_from_snapshot = _hosts._instruction_state_from_snapshot
_read_regular_bounded = _hosts._read_regular_bounded
_snapshot = _hosts._snapshot
_uninstall_plans = _hosts._uninstall_plans
detect_hosts = _hosts.detect_hosts


def collect_host_evidence(home: Path, plugin_root: Path, host: str = "all") -> dict:
    """Collect deterministic host observations without locks, recovery, or mutation."""
    canonical, _messages = _canonical(plugin_root)
    root = canonical[0] if canonical else _absolute(plugin_root)
    version = canonical[2] if canonical else None
    try:
        hashes = _active_hashes(root) if canonical else {}
    except (OSError, ValueError):
        hashes = {}
        canonical = None
        version = None
    manifests = {
        name: {
            "path": str(root / directory / "plugin.json"),
            "sha256": hashes.get(f"{directory}/plugin.json"),
            "version": version,
        }
        for name, directory in (
            ("claude", ".claude-plugin"), ("codex", ".codex-plugin"),
        )
    }
    canonical_evidence = {
        "root": str(root),
        "version": version,
        "hashes": hashes,
        "manifests": manifests,
    }
    statuses = detect_hosts(_normalized_lexical_path(Path(home).expanduser()))
    if host not in ("all", "claude", "codex"):
        raise ValueError(f"unsupported host: {host}")
    selected = statuses if host == "all" else [
        status for status in statuses if status.name == host
    ]
    observations = {}
    for status in selected:
        try:
            observations[status.name] = _collect_one_host(
                status, canonical_evidence, canonical is not None
            )
        except (OSError, RuntimeError, ValueError):
            observations[status.name] = _failed_host_observation(status)
    return {
        "schema_version": 1,
        "kind": "evergreen-host-evidence",
        "canonical": canonical_evidence,
        "hosts": observations,
    }


def validate_host_evidence(value, *, require_all=True):
    """Validate the closed raw-observation schema without accepting verdict fields."""
    if _contains_boolean(value):
        raise ValueError("host evidence cannot contain boolean verdicts")
    _evidence_object(value, {"schema_version", "kind", "canonical", "hosts"})
    if value["schema_version"] != 1 or value["kind"] != "evergreen-host-evidence":
        raise ValueError("host evidence identity is invalid")
    canonical = value["canonical"]
    _evidence_object(canonical, {"root", "version", "hashes", "manifests"})
    _evidence_string(canonical["root"])
    _optional_evidence_string(canonical["version"])
    _evidence_hash_map(canonical["hashes"])
    manifests = canonical["manifests"]
    _evidence_object(manifests, {"claude", "codex"})
    for manifest in manifests.values():
        _evidence_object(manifest, {"path", "sha256", "version"})
        _evidence_string(manifest["path"])
        _optional_evidence_hash(manifest["sha256"])
        _optional_evidence_string(manifest["version"])

    hosts = value["hosts"]
    if not isinstance(hosts, dict):
        raise ValueError("host evidence must contain separate Claude and Codex observations")
    expected_hosts = {"claude", "codex"} if require_all else set(hosts)
    if set(hosts) != expected_hosts or not set(hosts) <= {
        "claude", "codex"
    }:
        raise ValueError("host evidence must contain separate Claude and Codex observations")
    for observation in hosts.values():
        _validate_host_observation(observation)
    return value


def host_evidence_aligned(value, host):
    """Mechanically derive one host's alignment from raw trusted observations."""
    try:
        validate_host_evidence(value)
    except ValueError:
        return False
    if host not in ("claude", "codex"):
        return False
    canonical = value["canonical"]
    observation = value["hosts"][host]
    installed = observation["installed"]
    root = Path(canonical["root"])
    expected_skill = str(root / "skills" / "evergreen")
    expected_skill_hashes = {
        path: digest for path, digest in canonical["hashes"].items()
        if path.startswith("skills/evergreen/")
    }
    expected_command_hashes = {
        path: digest for path, digest in canonical["hashes"].items()
        if path == "bin/evergreen" or path.startswith("commands/")
    }
    manifest = canonical["manifests"][host]
    ownership = observation["ownership"]
    artifacts = installed["artifacts"]
    instruction = "CLAUDE.md" if host == "claude" else "AGENTS.md"
    host_root = Path(observation["resolved_root"])
    expected_uninstall = {
        str(host_root / instruction), str(host_root / OWNERSHIP_FILE),
        str(host_root / "skills" / "evergreen"),
    }
    chain_safe = bool(observation["resolution_chain"]) and all(
        item["uid"] == os.getuid()
        and not (
            item["kind"] == "directory" and item["mode"] is not None
            and item["mode"] & 0o022
        )
        for item in observation["resolution_chain"]
    )
    return all((
        isinstance(canonical["version"], str),
        bool(expected_skill_hashes),
        bool(expected_command_hashes),
        observation["doctor_issues"] == [],
        observation["installed"] == observation["discovery"],
        chain_safe,
        ownership is not None,
        ownership is not None and ownership["kind"] == "regular",
        ownership is not None and ownership["sha256"] is not None,
        ownership is not None and ownership["plugin_root"] == canonical["root"],
        ownership is not None and ownership["skill_target"] == expected_skill,
        _artifact_safe(artifacts["instructions"], "regular"),
        artifacts["instructions"]["path"] == str(host_root / instruction),
        artifacts["instructions"]["sha256"] is not None,
        _artifact_safe(artifacts["ownership"], "regular"),
        artifacts["ownership"]["path"] == str(host_root / OWNERSHIP_FILE),
        ownership is not None and artifacts["ownership"]["sha256"] == ownership["sha256"],
        _artifact_safe(artifacts["skill"], "symlink", protect_mode=False),
        artifacts["skill"]["path"] == str(host_root / "skills" / "evergreen"),
        artifacts["skill"]["target"] == expected_skill,
        _artifact_safe(artifacts["skills_parent"], "directory"),
        artifacts["skills_parent"]["path"] == str(host_root / "skills"),
        installed["resolved_root"] == observation["resolved_root"],
        installed["instruction_state"] == "owned",
        installed["instruction_block_sha256"] == hashlib.sha256(_block(root)).hexdigest(),
        installed["skill_kind"] == "symlink",
        installed["skill_target"] == expected_skill,
        installed["skill_hashes"] == expected_skill_hashes,
        installed["command_hashes"] == expected_command_hashes,
        installed["manifest_sha256"] == manifest["sha256"],
        installed["version"] == canonical["version"] == manifest["version"],
        set(observation["uninstall_owned_paths"]) == expected_uninstall,
    ))


def _validate_host_observation(value):
    _evidence_object(value, HOST_EVIDENCE_FIELDS)
    _evidence_string(value["lexical_root"])
    _evidence_string(value["resolved_root"])
    chain = value["resolution_chain"]
    if not isinstance(chain, list):
        raise ValueError("host resolution chain is invalid")
    for item in chain:
        _evidence_object(item, {"path", "kind", "uid", "mode"})
        _evidence_string(item["path"])
        _evidence_string(item["kind"])
        for field in ("uid", "mode"):
            if item[field] is not None and (type(item[field]) is not int or item[field] < 0):
                raise ValueError("host resolution metadata is invalid")
    ownership = value["ownership"]
    if ownership is not None:
        _evidence_object(
            ownership, {"path", "kind", "sha256", "plugin_root", "skill_target"}
        )
        _evidence_string(ownership["path"])
        _evidence_string(ownership["kind"])
        _optional_evidence_hash(ownership["sha256"])
        _optional_evidence_string(ownership["plugin_root"])
        _optional_evidence_string(ownership["skill_target"])
    _validate_installed_observation(value["installed"])
    _validate_installed_observation(value["discovery"])
    _sorted_unique_strings(value["doctor_issues"])
    _sorted_unique_strings(value["uninstall_owned_paths"])


def _validate_installed_observation(value):
    _evidence_object(value, INSTALLED_EVIDENCE_FIELDS)
    _evidence_string(value["resolved_root"])
    _evidence_string(value["instruction_state"])
    _optional_evidence_hash(value["instruction_block_sha256"])
    _validate_artifacts(value["artifacts"])
    _evidence_string(value["skill_kind"])
    _optional_evidence_string(value["skill_target"])
    _evidence_hash_map(value["skill_hashes"])
    _evidence_hash_map(value["command_hashes"])
    _optional_evidence_hash(value["manifest_sha256"])
    _optional_evidence_string(value["version"])


def _validate_artifacts(value):
    _evidence_object(value, {"instructions", "ownership", "skill", "skills_parent"})
    for artifact in value.values():
        _evidence_object(artifact, {"path", "kind", "sha256", "target", "uid", "mode"})
        _evidence_string(artifact["path"])
        _evidence_string(artifact["kind"])
        _optional_evidence_hash(artifact["sha256"])
        _optional_evidence_string(artifact["target"])
        for field in ("uid", "mode"):
            if artifact[field] is not None and (
                type(artifact[field]) is not int or artifact[field] < 0
            ):
                raise ValueError("host artifact metadata is invalid")


def _contains_boolean(value):
    if type(value) is bool:
        return True
    if isinstance(value, dict):
        return any(_contains_boolean(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_boolean(item) for item in value)
    return False


def _evidence_object(value, fields):
    if not isinstance(value, dict) or set(value) != set(fields):
        raise ValueError("host evidence fields are invalid")


def _evidence_string(value):
    if not isinstance(value, str) or not value:
        raise ValueError("host evidence string is invalid")


def _optional_evidence_string(value):
    if value is not None:
        _evidence_string(value)


def _optional_evidence_hash(value):
    if value is not None and (
        not isinstance(value, str) or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("host evidence hash is invalid")


def _evidence_hash_map(value):
    if not isinstance(value, dict):
        raise ValueError("host evidence hash map is invalid")
    for path, digest in value.items():
        if (
            not isinstance(path, str) or not path or PurePosixPath(path).is_absolute()
            or ".." in PurePosixPath(path).parts
        ):
            raise ValueError("host evidence hash path is invalid")
        _optional_evidence_hash(digest)
        if digest is None:
            raise ValueError("host evidence hash is missing")


def _sorted_unique_strings(value):
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
    ):
        raise ValueError("host evidence list is invalid")


def _active_hashes(root):
    _secure_active_path(root)
    files = _package_sources(root)
    for relative in (
        Path("AGENTS.md"), Path("bin/evergreen"), Path("commands"),
        Path("skills/evergreen"), Path(".claude-plugin/plugin.json"),
        Path(".codex-plugin/plugin.json"),
    ):
        path = root / relative
        _secure_active_path(path)
        kind = _kind(path)
        if kind == "regular":
            files.append(path)
        elif kind == "directory":
            for directory, names, filenames in os.walk(path, followlinks=False):
                _secure_active_path(Path(directory))
                for name in names:
                    candidate = Path(directory) / name
                    if _kind(candidate) != "directory":
                        raise OSError(f"unsafe active evidence directory: {candidate}")
                    _secure_active_path(candidate)
                names[:] = sorted(names)
                for filename in sorted(filenames):
                    candidate = Path(directory) / filename
                    if _kind(candidate) != "regular":
                        raise OSError(f"unsafe active evidence file: {candidate}")
                    _secure_active_path(candidate)
                    files.append(candidate)
        else:
            raise OSError(f"missing active evidence path: {path}")
    if len(files) > MAX_EVIDENCE_FILES:
        raise ValueError("active evidence file count exceeds limit")
    output = {}
    total = 0
    for path in sorted(set(files)):
        payload = _read_regular_bounded(path, MAX_COMMAND_BYTES, "active evidence file")
        total += len(payload)
        if total > MAX_EVIDENCE_BYTES:
            raise ValueError("active evidence bytes exceed limit")
        output[path.relative_to(root).as_posix()] = hashlib.sha256(payload).hexdigest()
    return output


def _package_sources(root):
    package = root / "evergreen"
    _secure_active_path(package)
    if _kind(package) != "directory":
        raise OSError(f"missing canonical package directory: {package}")
    expected = set(PACKAGE_SOURCES)
    for child in package.iterdir():
        if child.name in expected:
            continue
        if child.name.endswith(".py") or (
            _kind(child) == "directory" and child.name != "__pycache__"
        ):
            raise OSError(f"unexpected canonical package source: {child}")
    sources = []
    for name in PACKAGE_SOURCES:
        source = package / name
        if _kind(source) != "regular":
            raise OSError(f"missing canonical package source: {source}")
        _secure_active_path(source)
        if source.lstat().st_nlink != 1:
            raise OSError(f"hard-linked canonical package source: {source}")
        sources.append(source)
    return sources


def _secure_active_path(path):
    metadata = Path(path).lstat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
        raise OSError(f"unsafe writable active evidence path: {path}")


def _collect_one_host(status, canonical, canonical_valid):
    first, ownership_state, captured = _installed_observation(status, canonical)
    issues = _host_observation_issues(status, canonical, canonical_valid, first, ownership_state)
    uninstall_paths = []
    if ownership_state is not None and captured is not None:
        plans, plan_errors = _uninstall_plans(status, ownership_state, captured)
        if plan_errors:
            issues.append("uninstall-plan-invalid")
        else:
            uninstall_paths = sorted({
                str(plan[2]) for plan in plans if plan[0] in ("write", "delete")
            })
    second, _second_state, _second_captured = _installed_observation(status, canonical)
    if first != second:
        issues.append("fresh-discovery-mismatch")
    return {
        "lexical_root": str(status.root),
        "resolved_root": str(status.resolved_root),
        "resolution_chain": _resolution_chain(status),
        "ownership": _ownership_observation(status, captured, ownership_state),
        "installed": first,
        "doctor_issues": sorted(set(issues)),
        "discovery": second,
        "uninstall_owned_paths": uninstall_paths,
    }


def _installed_observation(status, canonical):
    captured = _capture_preflight([status])
    ownership_snapshot = captured[status.ownership]
    ownership_state, _error = _ownership_from_snapshot(status, ownership_snapshot)
    instruction_state, block = _instruction_state_from_snapshot(captured[status.instructions])
    skill = captured[status.skill]
    skill_target = (
        str(_normalized_snapshot_target(skill)) if skill.kind == "symlink" else None
    )
    expected_skill = Path(canonical["root"]) / "skills" / "evergreen"
    skill_hashes = {
        path: digest for path, digest in canonical["hashes"].items()
        if path.startswith("skills/evergreen/")
    } if skill_target == str(expected_skill) else {}
    ownership_aligned = (
        ownership_state is not None
        and ownership_state.plugin_root == canonical["root"]
        and ownership_state.skill_target == str(expected_skill)
    )
    command_hashes = {
        path: digest for path, digest in canonical["hashes"].items()
        if path == "bin/evergreen" or path.startswith("commands/")
    } if ownership_aligned else {}
    manifest = canonical["manifests"][status.name]
    return ({
        "resolved_root": str(status.resolved_root),
        "instruction_state": instruction_state,
        "instruction_block_sha256": hashlib.sha256(block).hexdigest() if block else None,
        "artifacts": _artifact_observations(status, captured),
        "skill_kind": skill.kind,
        "skill_target": skill_target,
        "skill_hashes": skill_hashes,
        "command_hashes": command_hashes,
        "manifest_sha256": manifest["sha256"] if ownership_aligned else None,
        "version": manifest["version"] if ownership_aligned else None,
    }, ownership_state, captured)


def _artifact_observations(status, captured):
    paths = {
        "instructions": status.instructions,
        "ownership": status.ownership,
        "skill": status.skill,
        "skills_parent": status.skill.parent,
    }
    return {
        name: _artifact_observation(captured[path]) for name, path in paths.items()
    }


def _artifact_observation(snapshot):
    return {
        "path": str(snapshot.path),
        "kind": snapshot.kind,
        "sha256": hashlib.sha256(snapshot.data).hexdigest() if snapshot.data is not None else None,
        "target": snapshot.target,
        "uid": snapshot.uid,
        "mode": snapshot.mode,
    }


def _artifact_safe(artifact, kind, *, protect_mode=True):
    return (
        artifact["kind"] == kind
        and artifact["uid"] == os.getuid()
        and artifact["mode"] is not None
        and (not protect_mode or not artifact["mode"] & 0o022)
    )


def _ownership_observation(status, captured, state):
    if captured is None:
        return None
    snapshot = captured[status.ownership]
    return {
        "path": str(status.ownership),
        "kind": snapshot.kind,
        "sha256": hashlib.sha256(snapshot.data).hexdigest() if snapshot.data is not None else None,
        "plugin_root": state.plugin_root if state is not None else None,
        "skill_target": state.skill_target if state is not None else None,
    }


def _resolution_chain(status):
    paths = status.managed_chain or (status.root,)
    output = []
    for path in paths:
        snapshot = _snapshot(path, allow_directory=True)
        output.append({
            "path": str(path), "kind": snapshot.kind,
            "uid": snapshot.uid, "mode": snapshot.mode,
        })
    return output


def _host_observation_issues(status, canonical, canonical_valid, installed, state):
    issues = []
    if not canonical_valid:
        issues.append("canonical-invalid")
    if not status.present:
        issues.append("host-root-absent")
    if status.problem:
        issues.append("host-root-unsafe")
    if state is None:
        issues.append("ownership-missing-or-invalid")
    else:
        if state.plugin_root != canonical["root"] or state.skill_target != str(
            Path(canonical["root"]) / "skills" / "evergreen"
        ):
            issues.append("ownership-stale")
    expected_block = hashlib.sha256(_block(Path(canonical["root"]))).hexdigest()
    if installed["instruction_state"] != "owned":
        issues.append("instruction-block-missing")
    elif installed["instruction_block_sha256"] != expected_block:
        issues.append("instruction-block-stale")
    if installed["skill_kind"] != "symlink":
        issues.append("skill-link-missing")
    elif installed["skill_target"] != str(Path(canonical["root"]) / "skills" / "evergreen"):
        issues.append("skill-link-stale")
    artifacts = installed["artifacts"]
    for name, kind, issue, protect_mode in (
        ("instructions", "regular", "instruction-file-unsafe", True),
        ("ownership", "regular", "ownership-file-unsafe", True),
        ("skill", "symlink", "skill-link-unsafe", False),
        ("skills_parent", "directory", "skills-parent-unsafe", True),
    ):
        if not _artifact_safe(artifacts[name], kind, protect_mode=protect_mode):
            issues.append(issue)
    expected_skill_hashes = {
        path: digest for path, digest in canonical["hashes"].items()
        if path.startswith("skills/evergreen/")
    }
    expected_command_hashes = {
        path: digest for path, digest in canonical["hashes"].items()
        if path == "bin/evergreen" or path.startswith("commands/")
    }
    if installed["skill_hashes"] != expected_skill_hashes:
        issues.append("skill-hash-mismatch")
    if installed["command_hashes"] != expected_command_hashes:
        issues.append("command-hash-mismatch")
    manifest = canonical["manifests"][status.name]
    if (
        installed["manifest_sha256"] != manifest["sha256"]
        or installed["version"] != canonical["version"]
    ):
        issues.append("manifest-version-mismatch")
    for item in _resolution_chain(status):
        if item["uid"] != os.getuid() or (
            item["kind"] == "directory" and item["mode"] is not None and item["mode"] & 0o022
        ):
            issues.append("resolved-root-unsafe")
    return issues


def _failed_host_observation(status):
    artifacts = {
        name: {
            "path": str(path), "kind": "unavailable", "sha256": None,
            "target": None, "uid": None, "mode": None,
        }
        for name, path in {
            "instructions": status.instructions, "ownership": status.ownership,
            "skill": status.skill, "skills_parent": status.skill.parent,
        }.items()
    }
    empty = {
        "resolved_root": str(status.resolved_root),
        "instruction_state": "unavailable", "instruction_block_sha256": None,
        "artifacts": artifacts,
        "skill_kind": "unavailable", "skill_target": None, "skill_hashes": {},
        "command_hashes": {}, "manifest_sha256": None, "version": None,
    }
    return {
        "lexical_root": str(status.root), "resolved_root": str(status.resolved_root),
        "resolution_chain": [], "ownership": None, "installed": empty,
        "doctor_issues": ["collection-failed"], "discovery": dict(empty),
        "uninstall_owned_paths": [],
    }
