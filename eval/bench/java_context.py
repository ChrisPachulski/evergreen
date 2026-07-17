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
PROTOCOL_V2 = "java-git-window-v2"
PROTOCOL_V3 = "java-git-window-v3"
PROTOCOLS = (PROTOCOL, PROTOCOL_V2, PROTOCOL_V3)
MAX_CONTEXT_BYTES = 64 * 1024
MAX_GREP_BYTES = 1024 * 1024
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_CANDIDATES = 256          # v1, frozen
MAX_CANDIDATES_V2 = 2048      # v2 raises the grep-candidate ceiling
MAX_THROWS_GAP = 4096         # v2 bounded bridge across an omitted `throws` clause
WINDOW_LINES = 200
MAX_CALLEES_V3 = 8            # distinct called names resolved per pair
MAX_CALLEE_DECLS_V3 = 2       # declaration windows kept per called name
CALLEE_WINDOW_LINES = 60      # lines kept from each callee declaration
JAVA_KEYWORDS = frozenset((
    "if", "for", "while", "switch", "catch", "return", "new", "throw", "this", "super",
    "assert", "synchronized", "do", "else", "try", "finally", "instanceof", "case",
    "break", "continue", "default", "void", "int", "long", "short", "byte", "char",
    "float", "double", "boolean",
))
CALL_SITE = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")
IDENT = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")
THROWS = IDENT | frozenset(", .<>")
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")
HASH = re.compile(r"^[0-9a-f]{64}$")
UNAVAILABLE_REASONS = {
    "unsupported-language", "invalid-pair-id", "mirror-unavailable", "commit-unavailable",
    "no-java-candidate", "no-exact-match", "ambiguous-exact-match", "source-too-large",
    "context-too-large", "git-command-failed", "too-many-candidates",
}


def _unavailable(reason, protocol=PROTOCOL):
    return {"status": "unavailable", "protocol": protocol, "reason": reason}


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


def _token_normalise(text):
    """Map each kept character to its raw offset while collapsing whitespace to a single space
    only between identifier characters and dropping comments outside string/char literals."""
    output = []
    positions = []
    pending = None
    index = 0
    length = len(text)
    while index < length:
        character = text[index]
        if character in "\"'":
            pending = None
            output.append(character)
            positions.append(index)
            index += 1
            while index < length:
                inner = text[index]
                output.append(inner)
                positions.append(index)
                if inner == "\\" and index + 1 < length:
                    index += 1
                    output.append(text[index])
                    positions.append(index)
                    index += 1
                    continue
                index += 1
                if inner == character:
                    break
            continue
        if character == "/" and index + 1 < length and text[index + 1] == "/":
            pending = index if pending is None else pending
            index += 2
            while index < length and text[index] != "\n":
                index += 1
            continue
        if character == "/" and index + 1 < length and text[index + 1] == "*":
            pending = index if pending is None else pending
            index += 2
            while index + 1 < length and not (text[index] == "*" and text[index + 1] == "/"):
                index += 1
            index += 2
            continue
        if character.isspace():
            if pending is None:
                pending = index
            index += 1
            continue
        if pending is not None:
            if output and output[-1] in IDENT and character in IDENT:
                output.append(" ")
                positions.append(pending)
            pending = None
        output.append(character)
        positions.append(index)
        index += 1
    return "".join(output), positions


def _method_anchor(method):
    """Split a normalised method into its `name(params)` anchor and its body."""
    open_paren = method.find("(")
    if open_paren <= 0:
        return None
    depth = 0
    close_paren = -1
    for index in range(open_paren, len(method)):
        if method[index] == "(":
            depth += 1
        elif method[index] == ")":
            depth -= 1
            if depth == 0:
                close_paren = index
                break
    if close_paren < 0:
        return None
    name_start = open_paren
    while name_start > 0 and method[name_start - 1] in IDENT:
        name_start -= 1
    if name_start == open_paren:
        return None
    return method[name_start:close_paren + 1], method[close_paren + 1:]


def _body_after(source, offset, body):
    """Return the index in source just past body at offset, bridging an omitted throws clause."""
    if source.startswith(body, offset):
        return offset + len(body)
    if body[:1] == "{" and source.startswith("throws", offset):
        index = offset + len("throws")
        limit = offset + MAX_THROWS_GAP
        while index < len(source) and index < limit and source[index] != "{":
            if source[index] not in THROWS:
                return -1
            index += 1
        if index < len(source) and source[index] == "{" and source.startswith(body, index):
            return index + len(body)
    return -1


def _matches_v2(source, method):
    """Token-aware fallback: anchor on the method name and full parameter list so a leading
    generic parameter or omitted throws clause in real source still recovers CASCADE's
    re-serialised method without binding to a differently-signed method."""
    normalised_source, positions = _token_normalise(source)
    normalised_method, _ignored = _token_normalise(method)
    anchor = _method_anchor(normalised_method)
    if anchor is None:
        return []
    name_params, body = anchor
    if not name_params or not body:
        return []
    found = []
    offset = 0
    while True:
        index = normalised_source.find(name_params, offset)
        if index < 0:
            break
        offset = index + 1
        if index > 0 and normalised_source[index - 1] in IDENT:
            continue
        end = _body_after(normalised_source, index + len(name_params), body)
        if end < 0:
            continue
        found.append((positions[index], positions[end - 1] + 1))
    return found


def _context(repo_name, commit, path, source, span, protocol=PROTOCOL):
    raw_start, raw_end = span
    method_start = source.count("\n", 0, raw_start) + 1
    method_end = source.count("\n", 0, raw_end) + 1
    lines = source.splitlines(keepends=True)
    start = max(1, method_start - WINDOW_LINES)
    end = min(len(lines), method_end + WINDOW_LINES)

    def build():
        text = "".join(lines[start - 1:end])
        return {
            "status": "available", "protocol": protocol,
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
            return _unavailable("context-too-large", protocol)
        if after >= before and after > 0:
            end -= 1
        else:
            start += 1
        value = build()
    return value


def _called_names(code, own_name):
    """Distinct called simple names in first-use order, excluding keywords and the method."""
    names = []
    seen = {own_name} | JAVA_KEYWORDS
    for match in CALL_SITE.finditer(code):
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names[:MAX_CALLEES_V3]


def _declaration_pattern(name):
    """A declaration-shaped line: modifiers, then the name and its parameter list, with no
    statement boundary between them — rejects ordinary call sites."""
    return (r"(public|protected|private|static)[^;={}]*[ \t]"
            + re.escape(name) + r"[ \t]*\(")


def _callee_snippets(repo, commit, names, window_text):
    """Bounded, deterministic callee-declaration windows from the same commit.

    Every failure is conservative: a name that cannot be resolved contributes nothing and
    never makes the row unavailable."""
    snippets = []
    sources = {}
    for name in names:
        if re.search(_declaration_pattern(name), window_text):
            continue  # already evidenced by the method window
        try:
            output = _git(
                repo, MAX_GREP_BYTES, "grep", "-n", "-E", "-e",
                _declaration_pattern(name), commit, "--", "*.java",
            ).decode("utf-8")
        except (OSError, UnicodeError, subprocess.TimeoutExpired, ValueError):
            continue
        hits = []
        for line in output.splitlines():
            parts = line.split(":", 3)
            if len(parts) < 3:
                continue
            path, line_number = parts[1], parts[2]
            if (Path(path).is_absolute() or ".." in Path(path).parts or
                    not line_number.isdigit()):
                continue
            hits.append((path, int(line_number)))
        for path, line_number in sorted(hits)[:MAX_CALLEE_DECLS_V3]:
            if path not in sources:
                try:
                    sources[path] = _git(
                        repo, MAX_SOURCE_BYTES, "show", f"{commit}:{path}"
                    ).decode("utf-8")
                except (OSError, UnicodeError, subprocess.TimeoutExpired, ValueError):
                    sources[path] = None
            source = sources[path]
            if source is None:
                continue
            lines = source.splitlines(keepends=True)
            if line_number > len(lines):
                continue
            end = min(len(lines), line_number + CALLEE_WINDOW_LINES - 1)
            text = "".join(lines[line_number - 1:end])
            if not text:
                continue
            snippets.append({
                "kind": "callee-window", "path": path,
                "start_line": line_number, "end_line": end, "text": text,
                "sha256": hashlib.sha256(text.encode()).hexdigest(),
            })
    return snippets


def _append_callees(context, repo, commit, pair):
    """Append callee windows to an available v3 context inside the global byte budget."""
    names = _called_names(pair.get("code", ""), pair.get("func", ""))
    if not names:
        return context
    window_text = context["snippets"][0]["text"]
    for snippet in _callee_snippets(repo, commit, names, window_text):
        candidate = copy.deepcopy(context)
        candidate["snippets"].append(snippet)
        if len(json.dumps(candidate, sort_keys=True, separators=(",", ":")).encode()) > \
                MAX_CONTEXT_BYTES:
            break
        context = candidate
    return context


def validate_context(context, protocol=PROTOCOL):
    """Validate and detach a context, enforcing the exact expected protocol string."""
    if protocol not in PROTOCOLS:
        raise ValueError("benchmark pair context protocol is unknown")
    if not isinstance(context, dict):
        raise ValueError("benchmark pair context must be an object")
    if context.get("protocol") != protocol or context.get("status") not in {
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
        maximum_snippets = (
            1 + MAX_CALLEES_V3 * MAX_CALLEE_DECLS_V3 if protocol == PROTOCOL_V3 else 1
        )
        if (not isinstance(source, dict) or
                set(source) != {"repo", "commit", "path", "sha256"} or
                not isinstance(snippets, list) or
                not 1 <= len(snippets) <= maximum_snippets):
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
        for index, snippet in enumerate(snippets):
            expected_kind = "method-window" if index == 0 else "callee-window"
            snippet_path = snippet.get("path") if isinstance(snippet, dict) else None
            valid_path = (snippet_path == path if index == 0 else (
                isinstance(snippet_path, str) and snippet_path and
                len(snippet_path) <= 4096 and not Path(snippet_path).is_absolute() and
                ".." not in Path(snippet_path).parts))
            if (not isinstance(snippet, dict) or set(snippet) != {
                    "kind", "path", "start_line", "end_line", "text", "sha256"} or
                    snippet.get("kind") != expected_kind or not valid_path or
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


def _collect(repo, commit, paths, matcher, frozen_decode):
    """Read each candidate object once and gather (path, source, span) matches.

    frozen_decode keeps v1's ordering, where an undecodable object is a ValueError charged as
    too-large; v2 charges only a genuine size overflow and skips an undecodable object."""
    matches = []
    too_large = False
    for path in paths:
        if Path(path).is_absolute() or ".." in Path(path).parts:
            continue
        try:
            raw = _git(repo, MAX_SOURCE_BYTES, "show", f"{commit}:{path}")
            if frozen_decode:
                source = raw.decode("utf-8")
        except ValueError:
            too_large = True
            continue
        except (OSError, UnicodeError, subprocess.TimeoutExpired):
            continue
        if not frozen_decode:
            try:
                source = raw.decode("utf-8")
            except UnicodeError:
                continue
        matches.extend((path, source, span) for span in matcher(source))
    return matches, too_large


def derive_context(pair, mirror_root, protocol=PROTOCOL):
    """Derive context using only bounded `git grep`, `cat-file`, and `show` object reads.

    v2 keeps the v1 exact match as rung 1 and, only when it finds nothing, falls back to a
    token-aware rung 2 that survives CASCADE's re-serialised signatures."""
    if protocol not in PROTOCOLS:
        raise ValueError("unknown context protocol")
    if pair.get("language", "python").casefold() != "java":
        return _unavailable("unsupported-language", protocol)
    identity = _identity(pair)
    if identity is None:
        return _unavailable("invalid-pair-id", protocol)
    owner, name, commit = identity
    root = Path(mirror_root).resolve()
    repo = root / owner / name
    try:
        resolved = repo.resolve(strict=True)
    except OSError:
        return _unavailable("mirror-unavailable", protocol)
    if not resolved.is_dir() or root not in resolved.parents:
        return _unavailable("mirror-unavailable", protocol)
    try:
        _git(resolved, 128, "cat-file", "-e", f"{commit}^{{commit}}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return _unavailable("commit-unavailable", protocol)
    func = pair.get("func")
    code = pair.get("code")
    if not isinstance(func, str) or not func.strip() or not isinstance(code, str) or not code.strip():
        return _unavailable("no-exact-match", protocol)
    try:
        output = _git(
            resolved, MAX_GREP_BYTES, "grep", "-l", "-F", "-e", func,
            commit, "--", "*.java",
        ).decode("utf-8")
    except OSError as error:
        return _unavailable(
            "no-java-candidate" if str(error) == "command exited 1" else "git-command-failed",
            protocol,
        )
    except (UnicodeError, subprocess.TimeoutExpired, ValueError):
        return _unavailable("git-command-failed", protocol)
    prefix = f"{commit}:"
    paths = sorted({line.removeprefix(prefix) for line in output.splitlines() if line})
    if not paths:
        return _unavailable("no-java-candidate", protocol)
    frozen = protocol == PROTOCOL
    if len(paths) > (MAX_CANDIDATES if frozen else MAX_CANDIDATES_V2):
        return _unavailable("git-command-failed" if frozen else "too-many-candidates", protocol)
    matches, too_large = _collect(
        resolved, commit, paths, lambda source: _matches(source, code), frozen
    )
    if not matches and not frozen:
        matches, more_large = _collect(
            resolved, commit, paths, lambda source: _matches_v2(source, code), frozen
        )
        too_large = too_large or more_large
    if len(matches) > 1:
        return _unavailable("ambiguous-exact-match", protocol)
    if not matches:
        return _unavailable("source-too-large" if too_large else "no-exact-match", protocol)
    path, source, span = matches[0]
    value = _context(f"{owner}/{name}", commit, path, source, span, protocol)
    if protocol == PROTOCOL_V3 and value.get("status") == "available":
        value = _append_callees(value, resolved, commit, pair)
    return validate_context(value, protocol)


def augment_rows(rows, mirror_root, protocol=PROTOCOL):
    result = copy.deepcopy(rows)
    for row in result:
        if row.get("language", "python").casefold() == "java":
            row["context"] = derive_context(row, mirror_root, protocol)
    return result
