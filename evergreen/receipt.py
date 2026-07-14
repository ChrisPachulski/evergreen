"""Deterministic, read-only repository and benchmark receipts."""

import json
import os
import re
import selectors
import signal
import shutil
import stat
import subprocess
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit


GIT_TIMEOUT_SECONDS = 5
MAX_GIT_OUTPUT_BYTES = 1_048_576
MAX_MANIFEST_BYTES = 1_048_576
PUBLICATION_KIND = "evergreen-benchmark-decision-publication"
RECEIPT_ATTEMPTS = 2

_GIT_EXECUTABLE = str(Path(shutil.which("git") or "git").resolve())
_REMOTE_SCHEMES = {"file", "git", "http", "https", "ssh"}


class ReceiptError(ValueError):
    pass


class ReceiptOperationalError(ReceiptError):
    pass


def build_receipt(repo: Path, benchmark_manifest: Path | None = None) -> dict:
    root = _repository_root(repo)
    benchmark = (
        None
        if benchmark_manifest is None
        else _benchmark_identity(root, benchmark_manifest)
    )
    for _attempt in range(RECEIPT_ATTEMPTS):
        before = _repository_snapshot(root)
        after = _repository_snapshot(root)
        if before != after:
            continue
        status = after["status"]
        origin = after["origin"]
        return {
            "schema_version": 1,
            "repository": {
                "root": str(root),
                "name": _repository_name(root, origin),
                "origin": origin,
                **status,
            },
            "release": {
                "local_tags": after["local_tags"],
                "external_state": "unverified",
            },
            "benchmark": benchmark,
        }
    raise ReceiptOperationalError("repository changed while receipt was collected")


def _git(root, *args, missing_ok=False, input_error=False):
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "LC_ALL": "C",
    }
    try:
        process = subprocess.Popen(
            [
                _GIT_EXECUTABLE,
                "--no-optional-locks",
                "--no-replace-objects",
                "-c",
                "core.fsmonitor=false",
                "-c",
                "status.renames=true",
                "-c",
                "maintenance.auto=false",
                "-c",
                "gc.auto=0",
                "-C",
                str(root),
                *args,
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        raise ReceiptOperationalError("Git command could not be executed") from None
    try:
        output = _bounded_process_output(process)
    except ReceiptOperationalError:
        _terminate_process_group(process)
        raise
    if process.returncode:
        if missing_ok:
            return None
        error = ReceiptError if input_error else ReceiptOperationalError
        raise error("Git command failed")
    try:
        return output.decode("utf-8")
    except UnicodeDecodeError:
        raise ReceiptOperationalError("Git output is not valid UTF-8") from None


def _bounded_process_output(process):
    deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
    output = bytearray()
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReceiptOperationalError("Git command timed out")
            events = selector.select(remaining)
            if not events:
                raise ReceiptOperationalError("Git command timed out")
            chunk = os.read(
                process.stdout.fileno(),
                min(65_536, MAX_GIT_OUTPUT_BYTES + 1 - len(output)),
            )
            if not chunk:
                break
            output.extend(chunk)
            if len(output) > MAX_GIT_OUTPUT_BYTES:
                raise ReceiptOperationalError("Git command produced too much output")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReceiptOperationalError("Git command timed out")
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise ReceiptOperationalError("Git command timed out") from None
        return bytes(output)
    finally:
        selector.close()
        process.stdout.close()


def _terminate_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        if process.poll() is None:
            process.kill()
    process.wait()


def _repository_root(repo):
    root = _git(
        Path(repo),
        "rev-parse",
        "--show-toplevel",
        input_error=True,
    ).strip()
    if not root:
        raise ReceiptError("Git repository root is missing")
    return Path(root).resolve()


def _origin(root):
    origin = _git(root, "remote", "get-url", "origin", missing_ok=True)
    return None if origin is None else _redact_remote(origin.strip())


def _repository_name(root, origin):
    path = None
    if origin and "://" in origin:
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return root.name
        if parsed.scheme in _REMOTE_SCHEMES:
            path = parsed.path
    elif origin:
        scp = re.fullmatch(r"(?:[^/@:\s]+@)?[^/:\s]+:(.+)", origin)
        if scp:
            path = scp.group(1)
        elif origin != "[redacted]":
            path = origin
    if not path:
        return root.name
    name = PurePosixPath(path.rstrip("/")).name
    if name.endswith(".git"):
        name = name[:-4]
    return name if name not in ("", ".", "..") else root.name


def _redact_remote(remote):
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*::", remote):
        return "[redacted]"
    try:
        parsed = urlsplit(remote)
        if "://" in remote and parsed.scheme not in _REMOTE_SCHEMES:
            return "[redacted]"
        remote = urlunsplit(parsed._replace(query="", fragment=""))
        if parsed.scheme and parsed.username is not None:
            host = parsed.hostname or ""
            if ":" in host:
                host = f"[{host}]"
            port = f":{parsed.port}" if parsed.port is not None else ""
            return urlunsplit((
                parsed.scheme,
                f"[redacted]@{host}{port}",
                parsed.path,
                "",
                "",
            ))
    except ValueError:
        return "[redacted]"
    scp = re.fullmatch(r"([^/@:\s]+)@([^:\s]+):(.+)", remote)
    if scp:
        return f"[redacted]@{scp.group(2)}:{scp.group(3)}"
    if remote.startswith(("/", "./", "../", "~/")):
        return remote
    return "[redacted]" if "@" in remote else remote


def _repository_snapshot(root):
    status = _status(root)
    head = status["head"]
    return {
        "status": status,
        "origin": _origin(root),
        "local_tags": sorted(filter(
            None, _git(root, "tag", "--points-at", head).splitlines()
        )),
    }


def _status(root):
    symbolic_branch = _git(
        root,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        missing_ok=True,
    )
    output = _git(
        root,
        "status",
        "--porcelain=v2",
        "--branch",
        "-z",
        "--untracked-files=all",
    )
    records = output.split("\0")
    head = upstream = None
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
    branch = None if symbolic_branch is None else symbolic_branch.strip()
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
    try:
        raw = _read_repo_file(root, manifest_name, max_bytes=MAX_MANIFEST_BYTES)
    except ReceiptError:
        raise
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
    judge_sha256 = provenance.get("judge_sha256")
    if not isinstance(judge_sha256, str) or not re.fullmatch(
        r"[0-9a-f]{64}", judge_sha256
    ):
        raise ReceiptError("benchmark judge SHA-256 is invalid")
    resolver = _optional_identity(provenance, "resolver")
    protocol = _optional_identity(provenance, "protocol")

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
        "judge_sha256": judge_sha256,
        "languages": sorted(required_languages),
        "manifest": manifest_name,
        "protocol": protocol,
        "provenance_commit": commit,
        "provider": provider,
        "report": report_name,
        "resolver": resolver,
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


def _optional_identity(container, field):
    if field not in container:
        return "unverified"
    return _nonempty_text(container[field], f"benchmark {field}")


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


def _open_repo_file(root, supplied, *, max_bytes=None):
    relative = _normalized_path(supplied)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
        file_flags |= os.O_CLOEXEC
    directory = None
    descriptor = None
    try:
        directory = os.open(str(root), directory_flags)
        parts = PurePosixPath(relative).parts
        for part in parts[:-1]:
            next_directory = os.open(part, directory_flags, dir_fd=directory)
            os.close(directory)
            directory = next_directory
        descriptor = os.open(parts[-1], file_flags, dir_fd=directory)
        metadata = os.fstat(descriptor)
    except OSError:
        if descriptor is not None:
            os.close(descriptor)
        raise ReceiptError("benchmark path must be a regular file") from None
    finally:
        if directory is not None:
            os.close(directory)
    if not stat.S_ISREG(metadata.st_mode):
        os.close(descriptor)
        raise ReceiptError("benchmark path must be a regular file")
    if max_bytes is not None and metadata.st_size > max_bytes:
        os.close(descriptor)
        raise ReceiptError("benchmark manifest is too large")
    return descriptor


def _read_repo_file(root, supplied, *, max_bytes):
    descriptor = _open_repo_file(root, supplied, max_bytes=max_bytes)
    output = bytearray()
    try:
        while len(output) <= max_bytes:
            chunk = os.read(
                descriptor,
                min(65_536, max_bytes + 1 - len(output)),
            )
            if not chunk:
                break
            output.extend(chunk)
    except OSError:
        raise ReceiptError("benchmark manifest could not be read") from None
    finally:
        os.close(descriptor)
    if len(output) > max_bytes:
        raise ReceiptError("benchmark manifest is too large")
    return bytes(output)


def _safe_repo_file(root, supplied, *, max_bytes=None):
    descriptor = _open_repo_file(root, supplied, max_bytes=max_bytes)
    os.close(descriptor)
