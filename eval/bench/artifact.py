"""Reproducible benchmark artifact metadata and serialization."""

import hashlib
import json
import os
import selectors
import subprocess
import time
from pathlib import Path

HASH_CHUNK_BYTES = 1024 * 1024
MAX_COMMAND_BYTES = 32 * 1024 * 1024
MAX_CLI_VERSION_BYTES = 64 * 1024


def sha256_file(path):
    """Hash a file without loading it into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path, repo):
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _process_bytes(command, max_bytes, timeout=10):
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    output = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise subprocess.TimeoutExpired(command, timeout)
            chunk = os.read(
                process.stdout.fileno(), min(HASH_CHUNK_BYTES, max_bytes + 1 - len(output))
            )
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > max_bytes:
                raise ValueError(f"command output exceeds {max_bytes} bytes")
        return_code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except Exception:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
    if return_code:
        raise OSError(f"command exited {return_code}")
    return bytes(output)


def _command_output(command, fallback="unavailable"):
    try:
        output = _process_bytes(command, MAX_CLI_VERSION_BYTES).decode("utf-8", "replace").strip()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return fallback
    return output or fallback


def _git_bytes(repo, *args):
    return _process_bytes(["git", "-C", str(repo), *args], MAX_COMMAND_BYTES)


def _untracked_hash(repo, payload):
    digest = hashlib.sha256()
    root = repo.resolve()
    for raw_path in sorted(item for item in payload.split(b"\0") if item):
        path = repo / os.fsdecode(raw_path)
        resolved_parent = path.parent.resolve()
        if resolved_parent != root and root not in resolved_parent.parents:
            raise ValueError("untracked path escapes repository")
        digest.update(len(raw_path).to_bytes(8, "big"))
        digest.update(raw_path)
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(b"file\0")
            digest.update(bytes.fromhex(sha256_file(path)))
        else:
            digest.update(b"other\0")
    return digest.hexdigest()


def git_identity(repo):
    """Capture a deterministic identity for both HEAD and dirty working-tree state."""
    try:
        commit = _git_bytes(repo, "rev-parse", "HEAD").decode().strip()
        tree = _git_bytes(repo, "rev-parse", "HEAD^{tree}").decode().strip()
        status = _git_bytes(
            repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        )
        diff = _git_bytes(repo, "diff", "--no-ext-diff", "--binary", "HEAD", "--")
        untracked = _git_bytes(repo, "ls-files", "--others", "--exclude-standard", "-z")
        untracked_sha256 = _untracked_hash(repo, untracked)
    except (OSError, UnicodeError, subprocess.TimeoutExpired, ValueError):
        return {
            "commit": "unavailable", "tree": "unavailable", "dirty": None,
            "status_sha256": "unavailable", "diff_sha256": "unavailable",
            "untracked_sha256": "unavailable",
        }
    return {
        "commit": commit,
        "tree": tree,
        "dirty": bool(status),
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "untracked_sha256": untracked_sha256,
    }


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
        "dataset": {"path": _display_path(dataset, repo), "sha256": sha256_file(dataset)},
        "skill": {"path": _display_path(skill, repo), "sha256": sha256_file(skill)},
        "judge": {"path": _display_path(judge, repo), "sha256": sha256_file(judge)},
        "git": git_identity(repo),
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


def load_json(path, max_bytes):
    """Load JSON with a hard byte ceiling."""
    path = Path(path)
    if path.stat().st_size > max_bytes:
        raise ValueError(f"artifact too large: {path}")
    with path.open("rb") as source:
        payload = source.read(max_bytes + 1)
    if len(payload) > max_bytes:
        raise ValueError(f"artifact too large: {path}")
    return json.loads(payload)


def resume_state(document, expected_metadata):
    """Validate a resumable envelope and return its accumulated accounting."""
    if isinstance(document, list):
        raise ValueError("legacy artifacts have unknown provenance and cannot be resumed")
    if not isinstance(document, dict) or document.get("schema_version") != 1:
        raise ValueError("unsupported benchmark artifact schema")
    if document.get("metadata") != expected_metadata:
        raise ValueError("benchmark artifact provenance does not match this run")
    rows = document.get("rows")
    timing = document.get("timing")
    if not isinstance(rows, list) or not isinstance(timing, dict):
        raise ValueError("benchmark artifact is missing rows or timing")
    started_at = timing.get("started_at")
    elapsed = timing.get("elapsed_seconds")
    if (not isinstance(started_at, str) or not isinstance(elapsed, (int, float)) or
            isinstance(elapsed, bool) or elapsed < 0):
        raise ValueError("benchmark artifact has invalid timing")
    usage = document.get("provider_usage")
    if usage is not None and not isinstance(usage, dict):
        raise ValueError("benchmark artifact has invalid provider usage")
    return {
        "rows": rows, "started_at": started_at, "elapsed_seconds": elapsed,
        "provider_usage": usage,
    }


def merge_usage(previous, current):
    """Accumulate numeric provider accounting without discarding prior values."""
    if previous is None:
        return _canonical(current) if current is not None else None
    if current is None:
        return _canonical(previous)
    if isinstance(previous, dict) and isinstance(current, dict):
        return {
            key: merge_usage(previous.get(key), current.get(key))
            for key in sorted(previous.keys() | current.keys())
        }
    if (isinstance(previous, (int, float)) and not isinstance(previous, bool) and
            isinstance(current, (int, float)) and not isinstance(current, bool)):
        return previous + current
    if previous == current:
        return _canonical(previous)
    raise ValueError("provider usage fields changed type or value while resuming")
