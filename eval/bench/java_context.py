"""Conservative, read-only Java context extraction from local Git object stores."""

import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess

try:
    from .artifact import _process_bytes
except ImportError:  # Direct script execution.
    from artifact import _process_bytes


PROTOCOL = "java-git-window-v1"
MAX_CONTEXT_BYTES = 64 * 1024
MAX_GREP_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 256
WINDOW_LINES = 200
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
UNAVAILABLE_REASONS = {
    "unsupported-language", "invalid-pair-id", "mirror-unavailable", "commit-unavailable",
    "no-java-candidate", "no-exact-match", "ambiguous-exact-match", "source-too-large",
    "context-too-large", "git-command-failed",
}


def _unavailable(reason):
    return {"status": "unavailable", "protocol": PROTOCOL, "reason": reason}


def _identity(pair):
    pair_id = pair.get("id")
    if not isinstance(pair_id, str):
        return None
    parts = pair_id.split("/")
    if (len(parts) < 4 or not SAFE_COMPONENT.fullmatch(parts[0]) or
            not SAFE_COMPONENT.fullmatch(parts[1]) or not COMMIT.fullmatch(parts[2])):
        return None
    return parts[0], parts[1], parts[2].lower()


def _git(repo, max_bytes, *args):
    command = ["git", "--no-optional-locks", "-C", str(repo), *args]
    return _process_bytes(command, max_bytes, timeout=10)


def _normalised_with_map(text):
    output = []
    positions = []
    pending_space = None
    for index, character in enumerate(text):
        if character.isspace():
            if output and output[-1] != " " and pending_space is None:
                pending_space = index
            continue
        if pending_space is not None:
            output.append(" ")
            positions.append(pending_space)
            pending_space = None
        output.append(character)
        positions.append(index)
    return "".join(output), positions


def _matches(source, method):
    normalised_source, positions = _normalised_with_map(source)
    normalised_method, _ignored = _normalised_with_map(method)
    if not normalised_method:
        return []
    found = []
    offset = 0
    while True:
        index = normalised_source.find(normalised_method, offset)
        if index < 0:
            break
        found.append((positions[index], positions[index + len(normalised_method) - 1] + 1))
        offset = index + 1
    return found


def _context(repo_name, commit, path, source, span):
    raw_start, raw_end = span
    method_start = source.count("\n", 0, raw_start) + 1
    method_end = source.count("\n", 0, raw_end) + 1
    lines = source.splitlines(keepends=True)
    start = max(1, method_start - WINDOW_LINES)
    end = min(len(lines), method_end + WINDOW_LINES)

    def build():
        text = "".join(lines[start - 1:end])
        return {
            "status": "available", "protocol": PROTOCOL,
            "source": {
                "repo": repo_name, "commit": commit, "path": path,
                "sha256": hashlib.sha256(source.encode()).hexdigest(),
            },
            "snippets": [{
                "kind": "method-window", "path": path,
                "start_line": start, "end_line": end, "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            }],
        }

    value = build()
    while len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()) > \
            MAX_CONTEXT_BYTES:
        before = method_start - start
        after = end - method_end
        if before <= 0 and after <= 0:
            return _unavailable("context-too-large")
        if after >= before and after > 0:
            end -= 1
        else:
            start += 1
        value = build()
    return value


def validate_context(context):
    """Validate and detach the exact context protocol accepted by model prompts."""
    if not isinstance(context, dict):
        raise ValueError("benchmark pair context must be an object")
    if context.get("protocol") != PROTOCOL or context.get("status") not in {
            "available", "unavailable"}:
        raise ValueError("benchmark pair context protocol or status is invalid")
    if context["status"] == "unavailable":
        if set(context) != {"status", "protocol", "reason"} or \
                context.get("reason") not in UNAVAILABLE_REASONS:
            raise ValueError("benchmark pair context unavailable reason is invalid")
    else:
        if set(context) != {"status", "protocol", "source", "snippets"}:
            raise ValueError("benchmark pair context fields are invalid")
        source = context.get("source")
        snippets = context.get("snippets")
        if (not isinstance(source, dict) or
                set(source) != {"repo", "commit", "path", "sha256"} or
                not isinstance(snippets, list) or len(snippets) != 1):
            raise ValueError("benchmark pair context source is invalid")
        repo_parts = source.get("repo", "").split("/")
        path = source.get("path")
        if (len(repo_parts) != 2 or any(not SAFE_COMPONENT.fullmatch(part)
                                        for part in repo_parts) or
                not COMMIT.fullmatch(source.get("commit", "")) or
                not HASH.fullmatch(source.get("sha256", "")) or
                not isinstance(path, str) or not path or len(path) > 4096 or
                Path(path).is_absolute() or ".." in Path(path).parts):
            raise ValueError("benchmark pair context source identity is invalid")
        snippet = snippets[0]
        if (not isinstance(snippet, dict) or set(snippet) != {
                "kind", "path", "start_line", "end_line", "text", "sha256"} or
                snippet.get("kind") != "method-window" or snippet.get("path") != path or
                type(snippet.get("start_line")) is not int or
                type(snippet.get("end_line")) is not int or
                snippet["start_line"] < 1 or snippet["end_line"] < snippet["start_line"] or
                not isinstance(snippet.get("text"), str) or not snippet["text"] or
                hashlib.sha256(snippet["text"].encode()).hexdigest() !=
                snippet.get("sha256")):
            raise ValueError("benchmark pair context snippet is invalid")
    canonical = json.dumps(context, sort_keys=True, separators=(",", ":")).encode()
    if len(canonical) > MAX_CONTEXT_BYTES:
        raise ValueError(f"benchmark pair context exceeds {MAX_CONTEXT_BYTES} bytes")
    return copy.deepcopy(context)


def derive_context(pair, mirror_root):
    """Derive context using only bounded `git grep`, `cat-file`, and `show` object reads."""
    if pair.get("language", "python").casefold() != "java":
        return _unavailable("unsupported-language")
    identity = _identity(pair)
    if identity is None:
        return _unavailable("invalid-pair-id")
    owner, name, commit = identity
    root = Path(mirror_root).resolve()
    repo = root / owner / name
    try:
        resolved = repo.resolve(strict=True)
    except OSError:
        return _unavailable("mirror-unavailable")
    if not resolved.is_dir() or root not in resolved.parents:
        return _unavailable("mirror-unavailable")
    try:
        _git(resolved, 128, "cat-file", "-e", f"{commit}^{{commit}}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return _unavailable("commit-unavailable")
    func = pair.get("func")
    code = pair.get("code")
    if not isinstance(func, str) or not func.strip() or not isinstance(code, str) or not code.strip():
        return _unavailable("no-exact-match")
    try:
        output = _git(
            resolved, MAX_GREP_BYTES, "grep", "-l", "-F", "-e", func,
            commit, "--", "*.java",
        ).decode("utf-8")
    except OSError as error:
        return _unavailable(
            "no-java-candidate" if str(error) == "command exited 1" else "git-command-failed"
        )
    except (UnicodeError, subprocess.TimeoutExpired, ValueError):
        return _unavailable("git-command-failed")
    prefix = f"{commit}:"
    paths = sorted({line.removeprefix(prefix) for line in output.splitlines() if line})
    if not paths:
        return _unavailable("no-java-candidate")
    if len(paths) > MAX_CANDIDATES:
        return _unavailable("git-command-failed")
    matches = []
    too_large = False
    for path in paths:
        if Path(path).is_absolute() or ".." in Path(path).parts:
            continue
        try:
            raw = _git(resolved, MAX_SOURCE_BYTES, "show", f"{commit}:{path}")
            source = raw.decode("utf-8")
        except ValueError:
            too_large = True
            continue
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            continue
        matches.extend((path, source, span) for span in _matches(source, code))
    if len(matches) > 1:
        return _unavailable("ambiguous-exact-match")
    if not matches:
        return _unavailable("source-too-large" if too_large else "no-exact-match")
    path, source, span = matches[0]
    return validate_context(_context(f"{owner}/{name}", commit, path, source, span))


def augment_rows(rows, mirror_root):
    result = copy.deepcopy(rows)
    for row in result:
        if row.get("language", "python").casefold() == "java":
            row["context"] = derive_context(row, mirror_root)
    return result
