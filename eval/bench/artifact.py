"""Reproducible benchmark artifact metadata and serialization."""

import hashlib
import json
import math
import os
import selectors
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

HASH_CHUNK_BYTES = 1024 * 1024
MAX_COMMAND_BYTES = 32 * 1024 * 1024
MAX_CLI_VERSION_BYTES = 64 * 1024
MAX_UNTRACKED_BYTES = 1024 * 1024 * 1024
MAX_UNTRACKED_FILES = 100_000
MAX_UNTRACKED_SECONDS = 30
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
VALID_CATEGORIES = {None, "direct-mismatch", "over-promise", "under-promise"}


def sha256_file(path, max_bytes=None, deadline=None):
    """Hash a file without loading it into memory."""
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as source:
        while chunk := source.read(HASH_CHUNK_BYTES):
            total += len(chunk)
            if max_bytes is not None and total > max_bytes:
                raise ValueError(f"file exceeds {max_bytes} bytes")
            if deadline is not None and time.monotonic() > deadline:
                raise ValueError("file hashing exceeded time limit")
            digest.update(chunk)
    return digest.hexdigest()


def read_bytes(path, max_bytes, timeout=30, label="artifact"):
    """Read a file with byte and wall-clock ceilings, including growth races."""
    path = Path(path)
    if path.stat().st_size > max_bytes:
        raise ValueError(f"{label} too large")
    deadline = time.monotonic() + timeout
    payload = bytearray()
    with path.open("rb") as source:
        while True:
            if time.monotonic() > deadline:
                raise ValueError(f"{label} read exceeded time limit")
            chunk = source.read(min(HASH_CHUNK_BYTES, max_bytes + 1 - len(payload)))
            if not chunk:
                break
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise ValueError(f"{label} too large")
    return bytes(payload)


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


def _untracked_hash(
    repo, payload, max_bytes=MAX_UNTRACKED_BYTES, timeout=MAX_UNTRACKED_SECONDS
):
    digest = hashlib.sha256()
    root = repo.resolve()
    raw_paths = sorted(item for item in payload.split(b"\0") if item)
    if len(raw_paths) > MAX_UNTRACKED_FILES:
        raise ValueError("too many untracked files")
    deadline = time.monotonic() + timeout
    remaining = max_bytes
    for raw_path in raw_paths:
        if time.monotonic() > deadline:
            raise ValueError("untracked hashing exceeded time limit")
        path = repo / os.fsdecode(raw_path)
        resolved_parent = path.parent.resolve()
        if resolved_parent != root and root not in resolved_parent.parents:
            raise ValueError("untracked path escapes repository")
        digest.update(len(raw_path).to_bytes(8, "big"))
        digest.update(raw_path)
        if path.is_symlink():
            target = os.fsencode(os.readlink(path))
            remaining -= len(target)
            if remaining < 0:
                raise ValueError("untracked files exceed byte limit")
            digest.update(b"symlink\0")
            digest.update(target)
        elif path.is_file():
            size = path.stat().st_size
            if size > remaining:
                raise ValueError("untracked files exceed byte limit")
            digest.update(b"file\0")
            digest.update(bytes.fromhex(sha256_file(path, remaining, deadline)))
            remaining -= size
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


def validate_benchmark_row(row, require_result):
    if not isinstance(row, dict):
        raise ValueError("benchmark row must be an object")
    if not isinstance(row.get("id"), str) or not row["id"]:
        raise ValueError("benchmark row id must be a non-empty string")
    if row.get("label") not in ("consistent", "inconsistent"):
        raise ValueError("benchmark row label is invalid")
    if "category" not in row:
        raise ValueError("benchmark row category is missing")
    category = row["category"]
    if category is not None and (not isinstance(category, str) or category not in VALID_CATEGORIES):
        raise ValueError("benchmark row category is invalid")
    language = row.get("language", "unknown")
    if not isinstance(language, str) or not language:
        raise ValueError("benchmark row language must be a non-empty string")
    if require_result and not isinstance(row.get("got"), dict):
        raise ValueError("benchmark row result must be an object")


def validate_input_hashes(
    metadata, dataset, skill, dataset_max_bytes=64 * 1024 * 1024,
    skill_max_bytes=1024 * 1024, timeout=30,
):
    deadline = time.monotonic() + timeout
    try:
        dataset_hash = sha256_file(Path(dataset), dataset_max_bytes, deadline)
    except ValueError:
        raise ValueError("dataset changed after provenance capture") from None
    if dataset_hash != metadata["dataset"]["sha256"]:
        raise ValueError("dataset changed after provenance capture")
    try:
        skill_hash = sha256_file(Path(skill), skill_max_bytes, deadline)
    except ValueError:
        raise ValueError("skill changed after provenance capture") from None
    if skill_hash != metadata["skill"]["sha256"]:
        raise ValueError("skill changed after provenance capture")


def valid_iso_time(value):
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def validate_usage(value):
    if not isinstance(value, dict):
        raise ValueError("provider usage must be an object")
    for key, item in value.items():
        if not isinstance(key, str) or not key:
            raise ValueError("provider usage keys must be non-empty strings")
        if isinstance(item, dict):
            validate_usage(item)
        elif (not isinstance(item, (int, float)) or isinstance(item, bool) or
              not math.isfinite(item) or item < 0):
            raise ValueError("provider usage values must be finite non-negative numeric counts")


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
    return json.loads(read_bytes(path, max_bytes, label="artifact"))


def resume_state(document, expected_metadata, dataset_rows=None):
    """Validate a resumable envelope and return its accumulated accounting."""
    if isinstance(document, list):
        raise ValueError("legacy artifacts have unknown provenance and cannot be resumed")
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int or
            document["schema_version"] != 1):
        raise ValueError("unsupported benchmark artifact schema")
    if document.get("metadata") != expected_metadata:
        raise ValueError("benchmark artifact provenance does not match this run")
    rows = document.get("rows")
    timing = document.get("timing")
    if not isinstance(rows, list) or not isinstance(timing, dict):
        raise ValueError("benchmark artifact is missing rows or timing")
    started_at = timing.get("started_at")
    elapsed = timing.get("elapsed_seconds")
    if (not valid_iso_time(started_at) or not isinstance(elapsed, (int, float)) or
            isinstance(elapsed, bool) or not math.isfinite(elapsed) or elapsed < 0):
        raise ValueError("benchmark artifact has invalid timing")
    usage = document.get("provider_usage")
    if usage is not None and not isinstance(usage, dict):
        raise ValueError("benchmark artifact has invalid provider usage")
    if usage is not None:
        validate_usage(usage)
    for row in rows:
        validate_benchmark_row(row, require_result=True)
    if dataset_rows is not None:
        expected = {}
        for row in dataset_rows:
            validate_benchmark_row(row, require_result=False)
            if row["id"] in expected:
                raise ValueError("dataset contains duplicate pair ids")
            expected[row["id"]] = row
        seen = set()
        for row in rows:
            if row["id"] in seen:
                raise ValueError("resumed rows contain duplicate pair ids")
            seen.add(row["id"])
            source = {key: value for key, value in row.items() if key != "got"}
            if expected.get(row["id"]) != source:
                raise ValueError("resumed row does not match hashed dataset")
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


def atomic_write_json(path, document, max_bytes=MAX_ARTIFACT_BYTES):
    """Stream bounded canonical JSON and atomically expose only the complete artifact."""
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    total = 0
    encoder = json.JSONEncoder(indent=2, sort_keys=True, allow_nan=False)
    try:
        with os.fdopen(descriptor, "wb") as output:
            for text in encoder.iterencode(document):
                chunk = text.encode("utf-8")
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError(f"generated artifact exceeds {max_bytes} bytes")
                output.write(chunk)
            if total + 1 > max_bytes:
                raise ValueError(f"generated artifact exceeds {max_bytes} bytes")
            output.write(b"\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
