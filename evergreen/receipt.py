"""Deterministic, read-only repository and benchmark receipts."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit


GIT_TIMEOUT_SECONDS = 5
MAX_GIT_OUTPUT_BYTES = 1_048_576
MAX_MANIFEST_BYTES = 1_048_576
PUBLICATION_KIND = "evergreen-benchmark-decision-publication"

_GIT_EXECUTABLE = str(Path(shutil.which("git") or "git").resolve())


class ReceiptError(ValueError):
    pass


def build_receipt(repo: Path, benchmark_manifest: Path | None = None) -> dict:
    root = _repository_root(repo)
    status = _status(root)
    receipt = {
        "schema_version": 1,
        "repository": {
            "root": str(root),
            "name": root.name,
            "origin": _origin(root),
            **status,
        },
        "release": {
            "local_tags": sorted(filter(
                None, _git(root, "tag", "--points-at", "HEAD").splitlines()
            )),
            "external_state": "unverified",
        },
        "benchmark": None,
    }
    if benchmark_manifest is not None:
        receipt["benchmark"] = _benchmark_identity(root, benchmark_manifest)
    return receipt


def _git(root, *args, missing_ok=False):
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    try:
        result = subprocess.run(
            [
                _GIT_EXECUTABLE,
                "--no-optional-locks",
                "--no-replace-objects",
                "-C",
                str(root),
                *args,
            ],
            check=False,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        raise ReceiptError("Git command timed out") from None
    except OSError:
        raise ReceiptError("Git command could not be executed") from None
    if result.returncode:
        if missing_ok:
            return None
        raise ReceiptError("Git command failed")
    if len(result.stdout) > MAX_GIT_OUTPUT_BYTES:
        raise ReceiptError("Git command produced too much output")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError:
        raise ReceiptError("Git output is not valid UTF-8") from None


def _repository_root(repo):
    root = _git(Path(repo), "rev-parse", "--show-toplevel").strip()
    if not root:
        raise ReceiptError("Git repository root is missing")
    return Path(root).resolve()


def _origin(root):
    origin = _git(root, "remote", "get-url", "origin", missing_ok=True)
    return None if origin is None else _redact_remote(origin.strip())


def _redact_remote(remote):
    parsed = urlsplit(remote)
    if parsed.scheme and parsed.username is not None:
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        try:
            port = f":{parsed.port}" if parsed.port is not None else ""
        except ValueError:
            port = ""
        return urlunsplit((
            parsed.scheme,
            f"[redacted]@{host}{port}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        ))
    return re.sub(r"^[^/@:\s]+@(?=[^:\s]+:)", "[redacted]@", remote)


def _status(root):
    output = _git(
        root,
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        "--untracked-files=all",
    )
    records = output.split("\0")
    head = branch = upstream = None
    ahead = behind = None
    staged = unstaged = untracked = 0
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        if record.startswith("# branch.oid "):
            head = record.removeprefix("# branch.oid ")
        elif record.startswith("# branch.head "):
            value = record.removeprefix("# branch.head ")
            branch = None if value == "(detached)" else value
        elif record.startswith("# branch.upstream "):
            upstream = record.removeprefix("# branch.upstream ")
        elif record.startswith("# branch.ab "):
            match = re.fullmatch(r"# branch\.ab \+(\d+) -(\d+)", record)
            if not match:
                raise ReceiptError("Git status branch counts are invalid")
            ahead, behind = map(int, match.groups())
        elif record.startswith(("1 ", "2 ", "u ")):
            x, y = record[2:4]
            staged += x != "."
            unstaged += y != "."
            if record.startswith("2 "):
                index += 1
        elif record.startswith("? "):
            untracked += 1
    if not head or head == "(initial)":
        raise ReceiptError("Git repository has no HEAD commit")
    if branch is None:
        upstream = None
        ahead = behind = None
    elif upstream is None:
        ahead = behind = None
    return {
        "branch": branch,
        "detached": branch is None,
        "head": head,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "clean": staged == unstaged == untracked == 0,
    }


def _benchmark_identity(root, benchmark_manifest):
    manifest_name = _normalized_path(benchmark_manifest)
    manifest_path = _safe_repo_file(
        root, manifest_name, max_bytes=MAX_MANIFEST_BYTES
    )
    try:
        raw = manifest_path.read_bytes()
    except OSError:
        raise ReceiptError("benchmark manifest could not be read") from None
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ReceiptError("benchmark manifest is too large")
    try:
        document = json.loads(
            raw.decode("utf-8"), parse_constant=_reject_json_constant
        )
    except UnicodeDecodeError:
        raise ReceiptError("benchmark manifest is not valid UTF-8") from None
    except ValueError:
        raise ReceiptError("benchmark manifest is not valid JSON") from None
    if not isinstance(document, dict):
        raise ReceiptError("benchmark manifest must be an object")
    if type(document.get("schema_version")) is not int or document["schema_version"] != 1:
        raise ReceiptError("benchmark manifest schema is invalid")
    if document.get("kind") != PUBLICATION_KIND:
        raise ReceiptError("benchmark manifest kind is invalid")

    evaluated_release = _nonempty_text(
        document.get("evaluated_release"), "evaluated release"
    )
    provenance = document.get("provenance")
    if not isinstance(provenance, dict):
        raise ReceiptError("benchmark provenance is invalid")
    provider = _nonempty_text(provenance.get("provider"), "benchmark provider")
    commit = provenance.get("commit")
    if not isinstance(commit, str) or not re.fullmatch(
        r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit
    ):
        raise ReceiptError("benchmark provenance commit is invalid")

    publication = document.get("publication")
    if not isinstance(publication, dict):
        raise ReceiptError("benchmark publication is invalid")
    required = publication.get("required_languages")
    if not isinstance(required, list) or not required:
        raise ReceiptError("benchmark required languages are invalid")
    required_languages = [
        _nonempty_text(language, "benchmark language") for language in required
    ]
    if len(set(required_languages)) != len(required_languages):
        raise ReceiptError("benchmark required languages are duplicated")

    artifacts = document.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ReceiptError("benchmark artifacts are invalid")
    artifact_languages = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ReceiptError("benchmark artifact is invalid")
        artifact_languages.append(
            _nonempty_text(artifact.get("language"), "benchmark artifact language")
        )
        _safe_repo_file(root, _manifest_path(artifact, "path"))
        dataset = artifact.get("dataset")
        if not isinstance(dataset, dict):
            raise ReceiptError("benchmark artifact dataset is invalid")
        _safe_repo_file(root, _manifest_path(dataset, "path"))
    if len(set(artifact_languages)) != len(artifact_languages):
        raise ReceiptError("benchmark artifact languages are duplicated")
    if set(artifact_languages) != set(required_languages):
        raise ReceiptError("benchmark artifact languages do not match publication")

    report = document.get("report")
    if not isinstance(report, dict):
        raise ReceiptError("benchmark report is invalid")
    report_name = _manifest_path(report, "path")
    _safe_repo_file(root, report_name)
    return {
        "artifact_count": len(artifacts),
        "evaluated_release": evaluated_release,
        "evidence_state": "declared_publication",
        "languages": sorted(required_languages),
        "manifest": manifest_name,
        "provenance_commit": commit,
        "provider": provider,
        "report": report_name,
    }


def _manifest_path(container, field):
    value = container.get(field)
    if not isinstance(value, str):
        raise ReceiptError("benchmark path is invalid")
    return _normalized_path(value)


def _reject_json_constant(_value):
    raise ValueError


def _nonempty_text(value, name):
    if not isinstance(value, str) or not value or not value.strip():
        raise ReceiptError(f"{name} is invalid")
    return value


def _normalized_path(supplied):
    value = str(supplied)
    pure = PurePosixPath(value)
    if (
        not value
        or value == "."
        or "\\" in value
        or "//" in value
        or pure.is_absolute()
        or value != pure.as_posix()
        or any(part in ("", ".", "..") for part in value.split("/"))
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ReceiptError("benchmark path must be normalized and repository-relative")
    return value


def _safe_repo_file(root, supplied, *, max_bytes=None):
    relative = _normalized_path(supplied)
    root = root.resolve()
    current = root
    try:
        for part in PurePosixPath(relative).parts:
            current /= part
            current.lstat()
            if current.is_symlink():
                raise ReceiptError("benchmark path must not contain symlinks")
        resolved = current.resolve(strict=True)
        if root not in resolved.parents:
            raise ReceiptError("benchmark path escapes repository")
        stat = resolved.stat()
    except ReceiptError:
        raise
    except (OSError, RuntimeError):
        raise ReceiptError("benchmark path must be a regular file") from None
    if not resolved.is_file():
        raise ReceiptError("benchmark path must be a regular file")
    if max_bytes is not None and stat.st_size > max_bytes:
        raise ReceiptError("benchmark manifest is too large")
    return resolved
