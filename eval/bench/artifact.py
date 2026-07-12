"""Reproducible benchmark artifact metadata and serialization."""

import hashlib
import json
import subprocess
from pathlib import Path


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path, repo):
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _command_output(command, fallback="unavailable"):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return fallback
    output = result.stdout.strip()
    return output if result.returncode == 0 and output else fallback


def _canonical(value):
    """Return a detached value with recursively deterministic dictionary order."""
    if isinstance(value, dict):
        return {key: _canonical(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical(item) for item in value]
    return value


def artifact_metadata(dataset: Path, repo: Path, settings: dict) -> dict:
    """Capture the immutable inputs needed to reproduce a benchmark run."""
    dataset = Path(dataset)
    repo = Path(repo)
    skill = repo / "skills" / "evergreen" / "SKILL.md"
    judge = repo / "eval" / "bench" / "run_bench.py"
    return {
        "dataset": {"path": _display_path(dataset, repo), "sha256": _sha256(dataset)},
        "skill": {"path": _display_path(skill, repo), "sha256": _sha256(skill)},
        "judge": {"path": _display_path(judge, repo), "sha256": _sha256(judge)},
        "git_commit": _command_output(["git", "-C", str(repo), "rev-parse", "HEAD"]),
        "cli_version": _command_output(["claude", "--version"]),
        "settings": _canonical(settings),
    }


def artifact_document(
    rows, metadata, *, started_at, elapsed_seconds, provider_usage=None
):
    """Build the versioned benchmark artifact envelope."""
    document = {
        "schema_version": 1,
        "metadata": _canonical(metadata),
        "timing": {
            "started_at": started_at,
            "elapsed_seconds": elapsed_seconds,
        },
        "rows": rows,
    }
    if provider_usage is not None:
        document["provider_usage"] = _canonical(provider_usage)
    return document


def dumps(document):
    """Serialize with stable key order and a trailing newline."""
    return json.dumps(document, indent=2, sort_keys=True) + "\n"
