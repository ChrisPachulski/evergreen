"""Additive candidate discovery from passive evidence and source-to-doc maps."""

import fnmatch
import json
import math
import os
import re
import stat
import subprocess
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .evidence import Evidence

MAX_FILE_BYTES = 1024 * 1024
MAX_MAPS = 1000
MAX_PATTERNS = 100
MAX_DOCS = 100
MAX_STRING_BYTES = 4096
MAX_PATH_SEGMENTS = 64
MAX_PATTERN_SEGMENTS = 64
MAX_CHANGED_PATHS = 10_000
MAX_EVIDENCE_ITEMS = 10_000
MAX_MATCH_WORK = 100_000
MAX_CANDIDATES = 10_000
MAX_REASONS_PER_CANDIDATE = 100
MAX_TOTAL_REASONS = 100_000
MAX_WARNINGS = 1000
MAX_EVIDENCE_METADATA_ITEMS = 1000
MAX_EVIDENCE_METADATA_DEPTH = 8
MAX_EVIDENCE_METADATA_BYTES = 16 * 1024
MAX_DOC_SEARCH_FILES = 1000
MAX_DOC_SEARCH_BYTES = 8 * 1024 * 1024
MAX_DOC_LIST_BYTES = 1024 * 1024
MAX_CONTRACT_SYMBOLS = 100
MAX_SOURCE_SCAN_BYTES = 8 * 1024 * 1024
MAX_SOURCE_SCAN_WORK = 1000
SOURCE_SCAN_TIMEOUT_SECONDS = 3
DOC_SEARCH_TIMEOUT_SECONDS = 3

# A symbol match is graded by how much the surrounding prose commits to it being code.
# A bare word is the weakest evidence there is: "the path must resolve to a regular file"
# matches the symbol `resolve` on a word boundary while documenting nothing about it.
SYMBOL_RANK_CODE_CONTEXT = 80
SYMBOL_RANK_DISTINCT_WORD = 45
SYMBOL_RANK_PLAIN_WORD = 20
# A symbol spread across the corpus is vocabulary, not a link. Measured against this repo's 50
# tracked docs, generic declarations land high (`result` 36%, `add` 20%) while the symbols that
# genuinely bind a doc to a source file stay low (`resolve` 8%, `resolve_v3` 2%), so the demotion
# thresholds sit between them. Demotion only reorders; it never suppresses a candidate.
COMMON_SYMBOL_DOC_RATIO = 0.30
FREQUENT_SYMBOL_DOC_RATIO = 0.20
COMMON_SYMBOL_MIN_DOCS = 4
SYMBOL_RANK_TIERS = (SYMBOL_RANK_CODE_CONTEXT, SYMBOL_RANK_DISTINCT_WORD, SYMBOL_RANK_PLAIN_WORD)
MAX_SYMBOL_MATCHES = 10_000

FENCE_RE = re.compile(rb"(?m)^[ \t]{0,3}(`{3,}|~{3,})[^\r\n]*\r?$")
# An inline span closes on its own line. Allowing one to run across lines let a stray backtick
# pair with the next one anywhere in the file and read every word between them as code.
INLINE_CODE_RE = re.compile(rb"(`+)([^\r\n]+?)\1")

DECLARATION_RE = re.compile(
    rb"(?m)^([ \t]*)((?:export[ \t]+(?:default[ \t]+)?)?"
    rb"(?:public[ \t]+|pub(?:\([^\r\n)]*\))?[ \t]+)?)(?:declare[ \t]+)?(?:async[ \t]+)?"
    # `static` is Rust-only (kept per-suffix below): elsewhere it is a member modifier whose
    # following word would be captured as a phantom name. The mut lookahead pins the same trap.
    rb"(class|struct|enum|protocol|interface|trait|actor|type|def|func|fn|function|const|let|var"
    rb"|static(?![ \t]+mut(?![A-Za-z0-9_])))[ \t]+"
    rb"(?:\(([^\r\n)]*)\)[ \t]*)?"
    # A binding whose value is require()/import() is an import alias, not a declaration. The
    # word-boundary lookahead pins the captured name at full length: without it the engine
    # backtracks inside the identifier and revives the match one character short (`fs` -> `f`).
    rb"([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_])(?![ \t]*=[ \t]*(?:require|import)[ \t]*\()"
)
# TypeScript/JavaScript class and interface members carry no declaration keyword; a name
# followed by `(` at member indentation is the only lexical signal available. Reachability is
# still gated below on the enclosing scope being a public type, so call statements inside
# function bodies never surface. ponytail: methods of exported const object literals stay
# unlisted -- tracking value scopes needs a real parser.
TS_MEMBER_RE = re.compile(
    rb"(?m)^([ \t]+)"
    rb"((?:(?:public|protected|private|static|readonly|abstract|override|async|get|set)[ \t]+)*)"
    rb"([A-Za-z_$][A-Za-z0-9_$]*)[ \t]*\("
)
TS_MEMBER_STOPWORDS = frozenset((
    b"if", b"for", b"while", b"switch", b"catch", b"do", b"else", b"return",
    b"function", b"new", b"typeof", b"delete", b"void", b"throw", b"await",
    b"yield", b"in", b"of", b"case", b"super", b"this", b"import", b"require",
))
TS_FAMILY_SUFFIXES = frozenset((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
# ESM import/export syntax is what gives one of these files a module boundary at all; a file
# without it is a script whose top-level names are globally reachable. CommonJS files draw the
# same boundary with `module.exports` / `exports.name` assignments instead.
ES_MODULE_MARKER_RE = re.compile(rb"(?m)^[ \t]*(?:export|import)(?=[ \t{*'\"])")
# `export { name, other as alias }` publishes declarations carrying no inline marker; the local
# name vouches the declaration and the alias is the name a reader (and a doc) actually sees.
# ponytail: `export {x} from './y'` re-exports can vouch for a same-named local -- separating
# them needs a real parser.
ES_EXPORT_LIST_RE = re.compile(rb"(?m)^[ \t]*export[ \t]*(?:type[ \t]+)?{((?s:[^}]*))}")
# CommonJS export shapes: `exports.name =` (optionally aliasing a local identifier),
# `module.exports = {...}`, `module.exports = name`.
CJS_EXPORT_RE = re.compile(
    rb"(?m)^[ \t]*(?:module[ \t]*\.[ \t]*)?exports[ \t]*"
    rb"(?:\.[ \t]*(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)[ \t]*="
    rb"[ \t]*(?P<value>[A-Za-z_$][A-Za-z0-9_$]*)?"
    rb"|=[ \t]*(?:{(?P<body>(?s:[^}]*))}|(?P<whole>[A-Za-z_$][A-Za-z0-9_$]*)))"
)
# RHS words that are expression syntax, not the local identifier an export alias vouches for.
CJS_VALUE_STOPWORDS = frozenset((
    b"function", b"async", b"class", b"new", b"require", b"import", b"await", b"void",
    b"null", b"undefined", b"true", b"false", b"typeof",
))
# Grouped Go declarations: `const (` / `var (` / `type (` followed by one entry per line
# until a `)` back at column 0. Entries carry no keyword of their own.
GO_GROUP_HEADER_RE = re.compile(rb"(?m)^(const|var|type)[ \t]*\([ \t]*\r?$")
GO_GROUP_CLOSE_RE = re.compile(rb"(?m)^\)")
GO_GROUP_ENTRY_RE = re.compile(rb"[ \t]+([A-Za-z_][A-Za-z0-9_]*)")
RECEIVER_IDENTIFIER_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_]*")
# String and comment bodies are where quoted code sits at line start and reads as a declaration
# -- and where a stray delimiter can otherwise pair with a later one and swallow real code (this
# file's own regex literals did exactly that to its self-scan). Each masker therefore consumes
# single-line strings and comments too, so a delimiter inside them can never open a span; matched
# spans are blanked with newlines kept so line numbers hold. Python string prefixes are consumed
# with the literal so an f-string's `f` never counts as an identifier. ponytail: lexer-free;
# literals in the remaining languages are a known ceiling pending a per-language lexer.
PY_LITERAL_RE = re.compile(
    rb"[rRbBuUfF]{0,2}(?P<q>'''|\"\"\")(?s:.*?)(?P=q)"
    rb"|[rRbBuUfF]{0,2}'[^'\r\n\\]*(?:\\[\s\S][^'\r\n\\]*)*'"
    rb"|[rRbBuUfF]{0,2}\"[^\"\r\n\\]*(?:\\[\s\S][^\"\r\n\\]*)*\""
    rb"|#[^\r\n]*"
)
TS_LITERAL_RE = re.compile(
    rb"/\*(?s:.*?)\*/"
    rb"|//[^\r\n]*"
    rb"|`[^`\\]*(?:\\[\s\S][^`\\]*)*`"
    rb"|'[^'\r\n\\]*(?:\\[\s\S][^'\r\n\\]*)*'"
    rb"|\"[^\"\r\n\\]*(?:\\[\s\S][^\"\r\n\\]*)*\""
)
# Rust: no single-quoted strings -- a lone quote is a lifetime (`'a`), so only the
# exactly-one-character char literal form is consumed. Raw strings pair their hash runs.
# ponytail: nested block comments stop at the first `*/`; a real lexer is the upgrade path.
RUST_LITERAL_RE = re.compile(
    rb"/\*(?s:.*?)\*/"
    rb"|//[^\r\n]*"
    rb"|b?r(?P<hash>#*)\"(?s:.*?)\"(?P=hash)"
    rb"|b?\"[^\"\r\n\\]*(?:\\[\s\S][^\"\r\n\\]*)*\""
    rb"|'(?:\\[\s\S]|[^'\r\n\\])'"
)
SWIFT_LITERAL_RE = re.compile(
    rb"/\*(?s:.*?)\*/"
    rb"|//[^\r\n]*"
    rb"|\"\"\"(?s:.*?)\"\"\""
    rb"|\"[^\"\r\n\\]*(?:\\[\s\S][^\"\r\n\\]*)*\""
)
_LITERAL_BLANK = bytes(byte if byte in (10, 13) else 32 for byte in range(256))
# Only a declaration a reader can reach from outside the file is a documentation contract, and
# nesting decides that before any naming convention does: a method inside a class is API, a
# closure inside a function never is. Indentation is the nesting signal because it is the only
# structure available without one parser per language, and it costs nothing to be wrong about --
# an unindented file reads as all module scope, which is exactly what this replaced.
TYPE_DECLARATION_KEYWORDS = frozenset(
    (b"class", b"struct", b"enum", b"protocol", b"interface", b"trait", b"actor", b"type")
)
# Visibility itself is only decided outright where a compiler decides it. Go exports on an
# uppercase initial and Rust on `pub`, with no exceptions to be wrong about. TypeScript's `export`
# and Java's `public` are not on this list: a `.ts` script file has no module boundary and a
# package-private Java type is still documented by its package, so demanding the marker there
# would drop real surfaces. Those fall back to the convention every language shares -- an explicit
# marker means public, a leading underscore means private, and anything else is assumed public.
UPPERCASE_EXPORT_SUFFIXES = frozenset((".go",))
MARKED_EXPORT_SUFFIXES = frozenset((".rs",))
EXEMPT_DOC_PARTS = {
    "adr", "adrs", "archive", "archives", "audit", "audits", "plans", "readiness",
    "roadmaps", "specs",
}
DATED_DOC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[-_.]|$)")


@dataclass(frozen=True)
class ImpactMap:
    sources: tuple[str, ...]
    docs: tuple[str, ...]


@dataclass(frozen=True)
class ImpactCandidate:
    path: str
    rank: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ImpactReport:
    candidates: tuple[ImpactCandidate, ...]
    warnings: tuple[str, ...]


class _DecodedObject(dict):
    __slots__ = ("duplicate_keys",)


def _decoded_object(pairs):
    value = _DecodedObject()
    duplicates = []
    for key, item in pairs:
        if key in value:
            duplicates.append(key)
            continue
        value[key] = item
    value.duplicate_keys = tuple(duplicates)
    return value


def _duplicate_key(value):
    stack = [value]
    while stack:
        current = stack.pop()
        if isinstance(current, _DecodedObject) and current.duplicate_keys:
            return current.duplicate_keys[0]
        if isinstance(current, dict):
            stack.extend(reversed(tuple(current.values())))
        elif isinstance(current, list):
            stack.extend(reversed(current))
    return None


def _text(value, name):
    if (not isinstance(value, str) or not value or
            len(value.encode("utf-8")) > MAX_STRING_BYTES or
            any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError(f"{name} must be a bounded string")
    return value


def _lexical(value, name):
    value = _text(value, name)
    pure = PurePosixPath(value)
    if ("\\" in value or pure.is_absolute() or value != pure.as_posix() or
            any(part in ("", ".", "..") for part in pure.parts)):
        raise ValueError(f"{name} must be normalized and repository-relative")
    return value, pure


def _path(value, repo, name="path"):
    value, pure = _lexical(value, name)
    if len(pure.parts) > MAX_PATH_SEGMENTS:
        raise ValueError(f"{name} has too many segments (maximum {MAX_PATH_SEGMENTS})")
    try:
        root = repo.resolve()
        current = root
        for part in pure.parts:
            current /= part
            try:
                current.lstat()
            except FileNotFoundError:
                break
            if current.is_symlink():
                current.resolve(strict=True)
        resolved = (root / value).resolve(strict=False)
    except (OSError, RuntimeError):
        raise ValueError(f"{name} cannot be resolved") from None
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"{name} escapes repository")
    return value


def _pattern(value):
    value, pure = _lexical(value, "source pattern")
    if len(pure.parts) > MAX_PATTERN_SEGMENTS:
        raise ValueError(
            f"source pattern has too many segments (maximum {MAX_PATTERN_SEGMENTS})"
        )
    index = 0
    while index < len(value):
        if value[index] == "]":
            raise ValueError("source pattern has invalid brackets")
        if value[index] != "[":
            index += 1
            continue
        start = index
        index += 1
        if index < len(value) and value[index] in "!^":
            index += 1
        if index < len(value) and value[index] == "]":
            index += 1
        end = value.find("]", index)
        slash = value.find("/", index)
        if end < 0 or end == start + 1 or 0 <= slash < end:
            raise ValueError("source pattern has invalid brackets")
        index = end + 1
    if any("**" in part and part != "**" for part in value.split("/")):
        raise ValueError("globstar must occupy a complete path segment")
    return value


def _glob_match(pattern, path, max_work):
    pattern_parts = []
    for part in pattern.split("/"):
        if part != "**" or not pattern_parts or pattern_parts[-1] != "**":
            pattern_parts.append(part)
    path_parts = tuple(path.split("/"))
    reachable = {0}
    work = 0
    for part in pattern_parts:
        if part == "**":
            start = min(reachable)
            transitions = len(path_parts) - start + 1
            if work + transitions > max_work:
                return False, max_work, True
            work += transitions
            reachable = set(range(start, len(path_parts) + 1))
        else:
            next_reachable = set()
            for index in reachable:
                if work >= max_work:
                    return False, work, True
                work += 1
                if index < len(path_parts) and fnmatch.fnmatchcase(path_parts[index], part):
                    next_reachable.add(index + 1)
            reachable = next_reachable
        if not reachable:
            return False, work, False
    return len(path_parts) in reachable, work, False


def _evidence_text(value, name, *, nullable=False, limit=8192):
    if value is None and nullable:
        return None
    if (not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit or
            any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError(f"{name} must be a bounded string without control characters")
    return value


def _evidence_metadata(value):
    if not isinstance(value, Mapping):
        raise ValueError("metadata must be an object")
    item_count = 0
    active = set()

    def normalize(current, depth):
        nonlocal item_count
        if depth > MAX_EVIDENCE_METADATA_DEPTH:
            raise ValueError("metadata exceeds depth limit")
        item_count += 1
        if item_count > MAX_EVIDENCE_METADATA_ITEMS:
            raise ValueError("metadata exceeds item limit")
        if isinstance(current, Mapping):
            identity = id(current)
            if identity in active:
                raise ValueError("metadata must not contain cycles")
            active.add(identity)
            try:
                pairs = []
                for key, item in current.items():
                    if not isinstance(key, str):
                        raise ValueError("metadata keys must be strings")
                    pairs.append((key, normalize(item, depth + 1)))
                return {key: item for key, item in sorted(pairs)}
            finally:
                active.remove(identity)
        if isinstance(current, (list, tuple)):
            identity = id(current)
            if identity in active:
                raise ValueError("metadata must not contain cycles")
            active.add(identity)
            try:
                return [normalize(item, depth + 1) for item in current]
            finally:
                active.remove(identity)
        if isinstance(current, str):
            return current
        if isinstance(current, float) and not math.isfinite(current):
            raise ValueError("metadata numbers must be finite")
        if current is None or isinstance(current, (bool, int, float)):
            return current
        raise ValueError("metadata must contain JSON values")

    normalized = normalize(value, 1)
    encoded = json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    if len(encoded.encode("utf-8")) > MAX_EVIDENCE_METADATA_BYTES:
        raise ValueError("metadata exceeds byte limit")
    return encoded


def _validated_evidence(item, repo):
    if not isinstance(item, Evidence):
        raise ValueError("item has invalid type")
    provider = _evidence_text(item.provider, "provider", limit=256)
    version = _evidence_text(item.version, "version", limit=256)
    evidence_type = _evidence_text(item.type, "type", limit=256)
    path = _path(item.path, repo, "path")
    if type(item.line) is not int or not 1 <= item.line <= 2_147_483_647:
        raise ValueError("line must be a positive integer")
    if (item.span is not None and
            (type(item.span) is not int or not 1 <= item.span <= 2_147_483_647)):
        raise ValueError("span must be null or a positive integer")
    symbol = _evidence_text(item.symbol, "symbol", nullable=True)
    old = _evidence_text(item.old, "old", nullable=True)
    current = _evidence_text(item.current, "current", nullable=True)
    if item.confidence not in ("deterministic", "advisory"):
        raise ValueError("confidence must be deterministic or advisory")
    metadata = _evidence_metadata(item.metadata)
    key = (
        provider, version, evidence_type, path, item.line, item.span or 0,
        symbol or "", old or "", current or "", item.confidence, metadata,
    )
    reason = f"evidence {provider}@{version} {evidence_type} ({item.confidence})"
    rank = 50 if item.confidence == "deterministic" else 40
    return key, path, rank, reason


def _safe_warning(value):
    value = "".join(
        character if ord(character) >= 32 and ord(character) != 127 else "?"
        for character in str(value)
    )
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_STRING_BYTES:
        value = encoded[:MAX_STRING_BYTES].decode("utf-8", errors="ignore")
    return value


class _WarningCollector:
    def __init__(self, values=()):
        self.values = set()
        self.truncated = False
        for value in values:
            self.add(value)

    def add(self, value):
        value = _safe_warning(value)
        if value in self.values:
            return
        capacity = max(MAX_WARNINGS - (1 if self.truncated else 0), 0)
        if len(self.values) < capacity:
            self.values.add(value)
            return
        self.truncated = True
        capacity = max(MAX_WARNINGS - 1, 0)
        retained = sorted(self.values | {value})[:capacity]
        self.values = set(retained)

    def result(self):
        values = set(self.values)
        if self.truncated and MAX_WARNINGS > 0:
            values.add(f"warnings truncated (maximum {MAX_WARNINGS})")
        return tuple(sorted(values))


def _map_record(value, repo):
    duplicate = _duplicate_key(value)
    if duplicate is not None:
        raise ValueError(f"duplicate JSON key: {duplicate}")
    if not isinstance(value, dict) or set(value) != {"sources", "docs"}:
        raise ValueError("map must contain only sources and docs")
    if (not isinstance(value["sources"], list) or not value["sources"] or
            len(value["sources"]) > MAX_PATTERNS):
        raise ValueError("sources must be a bounded non-empty array")
    if (not isinstance(value["docs"], list) or not value["docs"] or
            len(value["docs"]) > MAX_DOCS):
        raise ValueError("docs must be a bounded non-empty array")
    if (len({json.dumps(item, sort_keys=True) for item in value["sources"]}) !=
            len(value["sources"]) or
            len({json.dumps(item, sort_keys=True) for item in value["docs"]}) !=
            len(value["docs"])):
        raise ValueError("sources and docs must not contain duplicates")
    sources = tuple(sorted({_pattern(item) for item in value["sources"]}))
    docs = tuple(sorted({_path(item, repo, "doc path") for item in value["docs"]}))
    return ImpactMap(sources, docs)


def load_map(repo: Path) -> tuple[list[ImpactMap], list[str]]:
    """Load valid additive mappings from .evergreen-map.json."""
    repo = Path(repo)
    path = repo / ".evergreen-map.json"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return [], []
    except OSError as error:
        return [], [f"could not inspect map config: {error}"]
    if not stat.S_ISREG(info.st_mode):
        return [], ["map config must be a regular file"]
    try:
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            os.close(descriptor)
            return [], ["map config must be a regular file"]
        if opened.st_size > MAX_FILE_BYTES:
            os.close(descriptor)
            return [], [f"map config too large (maximum {MAX_FILE_BYTES} bytes)"]
        with os.fdopen(descriptor, "rb") as source:
            payload = source.read(MAX_FILE_BYTES + 1)
        if len(payload) > MAX_FILE_BYTES:
            return [], [f"map config too large (maximum {MAX_FILE_BYTES} bytes)"]
        root = json.loads(payload, object_pairs_hook=_decoded_object)
    except (OSError, UnicodeError) as error:
        return [], [f"could not read map config: {error}"]
    except (json.JSONDecodeError, RecursionError):
        return [], ["map config contains invalid JSON"]
    if isinstance(root, _DecodedObject) and root.duplicate_keys:
        return [], [f"map config contains duplicate JSON key: {root.duplicate_keys[0]}"]
    if (not isinstance(root, dict) or set(root) != {"version", "maps"} or
            type(root["version"]) is not int or root["version"] != 1 or
            not isinstance(root["maps"], list)):
        return [], ["map config must contain version 1 and a maps array"]
    if len(root["maps"]) > MAX_MAPS:
        return [], [f"map config has too many maps (maximum {MAX_MAPS})"]

    accepted = {}
    warnings = []
    for index, value in enumerate(root["maps"], 1):
        try:
            mapping = _map_record(value, repo)
        except (TypeError, ValueError, RecursionError) as error:
            warnings.append(f"map {index}: {error}")
            continue
        key = (mapping.sources, mapping.docs)
        if key in accepted:
            warnings.append(f"map {index}: duplicate map ignored")
            continue
        accepted[key] = mapping
    return [accepted[key] for key in sorted(accepted)], warnings


def _bounded_git_output(repo, arguments):
    try:
        process = subprocess.Popen(
            ["git", *arguments], cwd=repo, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None, False
    output = bytearray()
    deadline = time.monotonic() + DOC_SEARCH_TIMEOUT_SECONDS
    assert process.stdout is not None
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    truncated = False
    try:
        while True:
            if time.monotonic() >= deadline:
                truncated = True
                process.kill()
                break
            try:
                chunk = os.read(descriptor, min(64 * 1024, MAX_DOC_LIST_BYTES + 1 - len(output)))
            except BlockingIOError:
                chunk = None
            if chunk:
                output.extend(chunk)
                if len(output) > MAX_DOC_LIST_BYTES:
                    truncated = True
                    process.kill()
                    break
            elif chunk == b"" and process.poll() is not None:
                break
            elif process.poll() is not None:
                continue
            else:
                time.sleep(0.005)
        returncode = process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        process.kill()
        process.wait()
        return None, truncated
    finally:
        process.stdout.close()
    if returncode != 0:
        return None, truncated
    return bytes(output), truncated


def _tracked_living_docs(repo):
    payload, truncated = _bounded_git_output(
        repo, ["ls-files", "-z", "--", "*.md", "*.mdx", "*.rst"],
    )
    if payload is None:
        return [], truncated
    paths = []
    for encoded in payload.split(b"\0"):
        if not encoded:
            continue
        try:
            path = encoded.decode("utf-8")
            pure = PurePosixPath(path)
        except UnicodeError:
            continue
        lower_parts = {part.lower() for part in pure.parts}
        if (lower_parts & EXEMPT_DOC_PARTS or
                pure.name.lower().startswith("changelog") or
                DATED_DOC_RE.match(pure.name)):
            continue
        try:
            paths.append(_path(path, repo, "tracked doc"))
        except ValueError:
            continue
    paths = sorted(set(paths))
    if len(paths) > MAX_DOC_SEARCH_FILES:
        truncated = True
        paths = paths[:max(MAX_DOC_SEARCH_FILES, 0)]
    return paths, truncated


def _bounded_regular_bytes(path, limit):
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            return None
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if ((opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or
                    not stat.S_ISREG(opened.st_mode) or opened.st_size > limit):
                return None
            chunks = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            return payload if len(payload) <= limit else None
        finally:
            os.close(descriptor)
    except OSError:
        return None


def _declares_public(suffix, markers, name, receiver=None, module=False):
    """Decide one declaration's visibility by the rule its own language enforces."""
    if suffix in UPPERCASE_EXPORT_SUFFIXES:
        if receiver:
            # A Go method rides its receiver type: `func (r *config) Load()` is unreachable
            # however loudly the method name is exported.
            tokens = RECEIVER_IDENTIFIER_RE.findall(receiver)
            base = tokens[1] if len(tokens) > 1 else tokens[0] if tokens else b""
            if not base[:1].isupper():
                return False
        return name[:1].isupper()
    if suffix in MARKED_EXPORT_SUFFIXES:
        return bool(markers)
    if module:
        # Inside a real ESM module only the export marker crosses the file boundary.
        return bool(markers)
    return bool(markers) or not name.startswith(b"_")


def _blank_span(match):
    return match.group(0).translate(_LITERAL_BLANK)


def _masked_literals(payload, suffix):
    """Blank string/comment bodies so quoted code is neither a declaration nor a reference."""
    if suffix == ".py":
        return PY_LITERAL_RE.sub(_blank_span, payload)
    # ponytail: Go's literal shapes are the TS set (raw strings ride the template branch).
    if suffix in TS_FAMILY_SUFFIXES or suffix == ".go":
        return TS_LITERAL_RE.sub(_blank_span, payload)
    if suffix == ".rs":
        return RUST_LITERAL_RE.sub(_blank_span, payload)
    if suffix == ".swift":
        return SWIFT_LITERAL_RE.sub(_blank_span, payload)
    return payload


def _go_group_declarations(payload):
    """Yield (offset, keyword, name) for each entry of a grouped const/var/type declaration."""
    for header in GO_GROUP_HEADER_RE.finditer(payload):
        close = GO_GROUP_CLOSE_RE.search(payload, header.end())
        if close is None:
            continue
        keyword = header.group(1)
        depth = 0
        offset = payload.find(b"\n", header.end()) + 1
        while 0 < offset < close.start():
            newline = payload.find(b"\n", offset, close.start())
            stop = close.start() if newline == -1 else newline
            line = payload[offset:stop]
            if depth == 0:
                entry = GO_GROUP_ENTRY_RE.match(line)
                if entry:
                    yield offset, keyword, entry.group(1)
            depth += line.count(b"{") - line.count(b"}")
            offset = stop + 1


def _es_exports(payload):
    """Exported local names and the local -> published-name alias map for one ESM/CJS file."""
    exported = set()
    aliases = {}
    for match in ES_EXPORT_LIST_RE.finditer(payload):
        for entry in match.group(1).split(b","):
            words = RECEIVER_IDENTIFIER_RE.findall(entry)
            if words and words[0] == b"type" and len(words) > 1:
                words = words[1:]
            if not words:
                continue
            exported.add(words[0])
            if len(words) >= 3 and words[1] == b"as":
                aliases[words[0]] = words[2]
    module = False
    for match in CJS_EXPORT_RE.finditer(payload):
        module = True
        name, value, body, whole = match.group("name", "value", "body", "whole")
        if name and value and value not in CJS_VALUE_STOPWORDS:
            # `exports.publicApi = internal` reaches the declaration named `internal`
            # and publishes it as `publicApi`.
            exported.add(value)
            aliases[value] = name
        elif name:
            exported.add(name)
        for group in (body, whole):
            if group:
                exported.update(RECEIVER_IDENTIFIER_RE.findall(group))
    exported.discard(b"as")
    exported.discard(b"default")
    return exported, aliases, module


def _reachable_declarations(payload, suffix):
    """Yield (line, depth, kind, name) for each declaration reachable from outside the file.

    The payload must already be literal-masked (`_masked_literals`); callers that hold raw
    source go through `_public_declarations`, which masks once.
    """
    matches = [
        (match.start(), "decl", match)
        for match in DECLARATION_RE.finditer(payload)
        # Outside Rust, `static` is a member modifier; the member scan below owns that form.
        if match.group(3) != b"static" or suffix in MARKED_EXPORT_SUFFIXES
    ]
    module = False
    exported = set()
    aliases = {}
    if suffix in TS_FAMILY_SUFFIXES:
        exported, aliases, cjs = _es_exports(payload)
        module = cjs or ES_MODULE_MARKER_RE.search(payload) is not None
    if suffix in TS_FAMILY_SUFFIXES or suffix in UPPERCASE_EXPORT_SUFFIXES:
        # Members carry no keyword in TS/JS classes and Go interfaces alike.
        starts = {start for start, _flavor, _match in matches}
        matches.extend(
            (match.start(), "member", match)
            for match in TS_MEMBER_RE.finditer(payload)
            if match.start() not in starts and match.group(3) not in TS_MEMBER_STOPWORDS
        )
    if suffix == ".go":
        matches.extend(
            (offset, "group", (keyword, name))
            for offset, keyword, name in _go_group_declarations(payload)
        )
    matches.sort(key=lambda item: item[0])
    # Indentation is the nesting signal, but it cannot see a scope whose braces open and close
    # on one line; brace depth closes that hole everywhere braces delimit scopes (not Python,
    # where a brace is data).
    braces = suffix != ".py"
    brace_depth = 0
    scopes = []
    line, consumed = 1, 0
    for start, flavor, item in matches:
        line += payload.count(b"\n", consumed, start)
        if braces:
            brace_depth += (payload.count(b"{", consumed, start) -
                            payload.count(b"}", consumed, start))
        consumed = start
        if flavor == "group":
            keyword, name = item
            if _declares_public(suffix, b"", name):
                yield line, 0, keyword.decode("ascii"), name.decode("ascii")
            continue
        is_member = flavor == "member"
        if is_member:
            indent, markers, name = item.groups()
            keyword = b"method"
            if suffix in UPPERCASE_EXPORT_SUFFIXES:
                public = name[:1].isupper()
            else:
                public = b"private" not in markers.split() and not name.startswith(b"_")
        else:
            indent, markers, keyword, receiver, name = item.groups()
            public = (_declares_public(suffix, markers, name, receiver, module) or
                      name in exported)
        while scopes and (scopes[-1][0] >= len(indent) or scopes[-1][3] >= brace_depth):
            scopes.pop()
        # Reachability outranks whatever a declaration calls itself: a closure cannot be imported
        # however loudly it is exported, and a member of a private type is private with it.
        if is_member:
            # A member is API only through a public type; anywhere else the same shape is a
            # call statement or a closure body.
            if not (scopes and scopes[-1][1] and scopes[-1][2]):
                public = False
        elif scopes and not (scopes[-1][1] and scopes[-1][2]):
            public = False
        depth = len(scopes)
        scopes.append((
            len(indent), not is_member and keyword in TYPE_DECLARATION_KEYWORDS, public,
            brace_depth if braces else -1,
        ))
        if public:
            name = aliases.get(name, name)
            yield line, depth, keyword.decode("ascii"), name.decode("ascii")


def _public_declarations(payload, suffix):
    """Names in one source file that could be a public documentation contract."""
    masked = _masked_literals(payload, suffix)
    return {name for _line, _depth, _kind, name in _reachable_declarations(masked, suffix)}


def _contract_symbols(repo, paths):
    symbols = set()
    scanned_bytes = 0
    work = 0
    deadline = time.monotonic() + SOURCE_SCAN_TIMEOUT_SECONDS
    truncated = False
    for path in paths:
        if (work >= MAX_SOURCE_SCAN_WORK or scanned_bytes >= MAX_SOURCE_SCAN_BYTES or
                time.monotonic() >= deadline):
            truncated = True
            break
        remaining = MAX_SOURCE_SCAN_BYTES - scanned_bytes
        try:
            source_metadata = (repo / path).lstat()
        except OSError:
            source_metadata = None
        if (source_metadata is not None and stat.S_ISREG(source_metadata.st_mode) and
                source_metadata.st_size > remaining):
            truncated = True
            break
        payload = _bounded_regular_bytes(repo / path, min(MAX_FILE_BYTES, remaining))
        work += 1
        if payload is None:
            continue
        if time.monotonic() >= deadline:
            truncated = True
            break
        scanned_bytes += len(payload)
        symbols.update(_public_declarations(payload, PurePosixPath(path).suffix.lower()))
        if len(symbols) > MAX_CONTRACT_SYMBOLS:
            truncated = True
            break
    return sorted(symbols)[:max(MAX_CONTRACT_SYMBOLS, 0)], truncated


def _code_context_spans(payload):
    """Byte ranges where a doc is quoting code: fenced blocks and inline code spans."""
    spans = []
    fence_open = None
    fence_marker = None
    for match in FENCE_RE.finditer(payload):
        marker = match.group(1)
        if fence_open is None:
            fence_open, fence_marker = match.start(), marker
        elif marker[:1] == fence_marker[:1] and len(marker) >= len(fence_marker):
            spans.append((fence_open, match.end()))
            fence_open = fence_marker = None
    # A fence that never closes claims nothing. Running it to end of file would let one stray
    # ``` -- or a ~~~ that a later ``` cannot close -- promote the whole remaining document to
    # code context, which is how prose comes to outrank a real reference.
    fenced = list(spans)
    for match in INLINE_CODE_RE.finditer(payload):
        if not any(start <= match.start() < end for start, end in fenced):
            spans.append((match.start(), match.end()))
    spans.sort()
    return spans


def _in_span(spans, offset):
    for start, end in spans:
        if start <= offset < end:
            return True
        if start > offset:
            break
    return False


def _is_distinct_identifier(symbol):
    """True when the bare word could not plausibly be English prose."""
    return ("_" in symbol or any(character.isdigit() for character in symbol) or
            any(character.isupper() for character in symbol))


def _symbol_match_rank(symbol, payload, spans, match):
    """Grade one symbol occurrence by how strongly its context commits to code."""
    if _in_span(spans, match.start()):
        return SYMBOL_RANK_CODE_CONTEXT
    after = payload[match.end():match.end() + 1]
    before = payload[max(match.start() - 2, 0):match.start()]
    if after == b"(" or after == b"." or before.endswith(b".") or before.endswith(b"::"):
        return SYMBOL_RANK_CODE_CONTEXT
    if _is_distinct_identifier(symbol):
        return SYMBOL_RANK_DISTINCT_WORD
    return SYMBOL_RANK_PLAIN_WORD


def impact(repo: Path, paths: list[str], evidence: list[Evidence]) -> ImpactReport:
    """Return additive candidates; maps never suppress and limits retain caller-order prefixes."""
    repo = Path(repo)
    mappings, map_warnings = load_map(repo)
    warnings = _WarningCollector(map_warnings)
    candidates = {}
    baseline_paths = set()
    reasons_truncated = False
    candidates_truncated = False

    def candidate_limit():
        if MAX_REASONS_PER_CANDIDATE <= 0:
            return 0
        return max(min(MAX_CANDIDATES, MAX_TOTAL_REASONS), 0)

    def selection_key(path):
        rank = candidates[path][0]
        return (path not in baseline_paths, -rank, path.lower(), path)

    def add(path, rank, reason):
        nonlocal candidates, candidates_truncated, reasons_truncated
        current_rank, reasons = candidates.get(path, (0, set()))
        reasons = set(reasons)
        reasons.add(reason)
        if len(reasons) > MAX_REASONS_PER_CANDIDATE:
            reasons = set(sorted(reasons)[:MAX_REASONS_PER_CANDIDATE])
            reasons_truncated = True
        candidates[path] = (max(current_rank, rank), reasons)
        # Bound discovery memory while retaining the same candidates that final ordering selects.
        limit = candidate_limit()
        threshold = max(limit * 2, 1)
        if len(candidates) > threshold:
            retained = sorted(candidates, key=selection_key)[:limit]
            candidates = {item: candidates[item] for item in retained}
            candidates_truncated = True

    if type(paths) not in (list, tuple):
        warnings.add("changed paths must be a concrete list or tuple")
        paths = ()
    changed_truncated = len(paths) > MAX_CHANGED_PATHS
    bounded_paths = paths[:max(MAX_CHANGED_PATHS, 0)]
    changed_paths = set()
    for raw_path in bounded_paths:
        try:
            path = _path(raw_path, repo, "changed path")
        except (TypeError, ValueError, UnicodeError) as error:
            warnings.add(f"changed path: {error}")
            continue
        changed_paths.add(path)
    changed_paths = sorted(changed_paths)
    if changed_truncated:
        warnings.add(f"changed paths truncated (maximum {MAX_CHANGED_PATHS})")

    if type(evidence) not in (list, tuple):
        warnings.add("evidence must be a concrete list or tuple")
        evidence = ()
    evidence_truncated = len(evidence) > MAX_EVIDENCE_ITEMS
    bounded_evidence = evidence[:max(MAX_EVIDENCE_ITEMS, 0)]
    accepted_evidence = {}
    for item in bounded_evidence:
        try:
            key, path, rank, reason = _validated_evidence(item, repo)
        except Exception as error:
            warnings.add(f"evidence: {error}")
            continue
        accepted_evidence[key] = (path, rank, reason)
    evidence_keys = sorted(accepted_evidence)
    if evidence_truncated:
        warnings.add(f"evidence items truncated (maximum {MAX_EVIDENCE_ITEMS})")

    sources = set(changed_paths)
    baseline_paths.update(changed_paths)
    for path in changed_paths:
        add(path, 10, f"changed path {path}")
    for key in evidence_keys:
        path, rank, reason = accepted_evidence[key]
        sources.add(path)
        add(path, rank, reason)

    work = 0
    work_truncated = False

    def spend_work():
        nonlocal work, work_truncated
        if work >= MAX_MATCH_WORK:
            work_truncated = True
            return False
        work += 1
        return True

    for source in sorted(sources):
        for mapping in mappings:
            for pattern in mapping.sources:
                matched, consumed, exhausted = _glob_match(
                    pattern, source, max(MAX_MATCH_WORK - work, 0)
                )
                work += consumed
                if exhausted:
                    work_truncated = True
                    break
                if matched:
                    reason = f"map {pattern} matched {source}"
                    for doc in mapping.docs:
                        if not spend_work():
                            break
                        add(doc, 100, reason)
                if work_truncated:
                    break
            if work_truncated:
                break
        if work_truncated:
            break
    if work_truncated:
        warnings.add(f"matching work truncated (maximum {MAX_MATCH_WORK} operations)")

    living_docs, docs_truncated = _tracked_living_docs(repo)
    if docs_truncated:
        warnings.add(
            f"living docs truncated (maximum {MAX_DOC_SEARCH_FILES} files and "
            f"{MAX_DOC_LIST_BYTES} path bytes)"
        )
    symbols, source_scan_truncated = _contract_symbols(repo, changed_paths)
    if source_scan_truncated:
        warnings.add(
            "source contract scan truncated "
            f"(maximum {MAX_SOURCE_SCAN_WORK} files, {MAX_SOURCE_SCAN_BYTES} bytes, "
            f"or {SOURCE_SCAN_TIMEOUT_SECONDS} seconds)"
        )
    search_bytes = 0
    symbol_matches = []
    symbol_document_frequency = {}
    scanned_docs = 0
    for doc in living_docs:
        remaining = MAX_DOC_SEARCH_BYTES - search_bytes
        if remaining <= 0:
            warnings.add(f"living doc search truncated (maximum {MAX_DOC_SEARCH_BYTES} bytes)")
            break
        try:
            doc_size = (repo / doc).lstat().st_size
        except OSError:
            continue
        if doc_size > remaining:
            warnings.add(f"living doc search truncated (maximum {MAX_DOC_SEARCH_BYTES} bytes)")
            break
        payload = _bounded_regular_bytes(repo / doc, min(MAX_FILE_BYTES, remaining))
        if payload is None:
            continue
        search_bytes += len(payload)
        scanned_docs += 1
        for source in changed_paths:
            if not spend_work():
                break
            if source.encode("utf-8") in payload:
                add(doc, 80, f"living doc mentions changed path {source}")
        if work_truncated:
            break
        spans = _code_context_spans(payload)
        for symbol in symbols:
            if not spend_work():
                break
            encoded = symbol.encode("utf-8")
            pattern = (rb"(?<![A-Za-z0-9_])" + re.escape(encoded) + rb"(?![A-Za-z0-9_])")
            best = None
            for match in re.finditer(pattern, payload):
                rank = _symbol_match_rank(symbol, payload, spans, match)
                if best is None or rank > best:
                    best = rank
                if best == SYMBOL_RANK_CODE_CONTEXT:
                    break
            if best is None:
                continue
            symbol_document_frequency[symbol] = symbol_document_frequency.get(symbol, 0) + 1
            if len(symbol_matches) < MAX_SYMBOL_MATCHES:
                symbol_matches.append((doc, symbol, best))
        if work_truncated:
            break
    if work_truncated:
        warnings.add(f"matching work truncated (maximum {MAX_MATCH_WORK} operations)")

    common = max(COMMON_SYMBOL_MIN_DOCS, scanned_docs * COMMON_SYMBOL_DOC_RATIO)
    frequent = max(COMMON_SYMBOL_MIN_DOCS, scanned_docs * FREQUENT_SYMBOL_DOC_RATIO)
    for doc, symbol, rank in symbol_matches:
        frequency = symbol_document_frequency.get(symbol, 0)
        steps = 2 if frequency >= common else 1 if frequency >= frequent else 0
        index = min(SYMBOL_RANK_TIERS.index(rank) + steps, len(SYMBOL_RANK_TIERS) - 1)
        add(doc, SYMBOL_RANK_TIERS[index], f"living doc mentions contract symbol {symbol}")

    retained_paths = sorted(candidates, key=selection_key)
    limit = candidate_limit()
    if len(retained_paths) > limit:
        retained_paths = retained_paths[:limit]
        candidates_truncated = True
    ordered_paths = sorted(
        retained_paths, key=lambda item: (-candidates[item][0], item.lower(), item)
    )
    if candidates_truncated:
        warnings.add(f"candidates truncated (maximum {limit})")

    reason_lists = {path: sorted(candidates[path][1]) for path in ordered_paths}
    allocated = {path: reasons[:1] for path, reasons in reason_lists.items()}
    remaining_reasons = MAX_TOTAL_REASONS - len(ordered_paths)
    for path in ordered_paths:
        extras = reason_lists[path][1:]
        accepted = extras[:max(remaining_reasons, 0)]
        allocated[path].extend(accepted)
        remaining_reasons -= len(accepted)
        if len(accepted) < len(extras):
            reasons_truncated = True
    result_items = []
    for path in ordered_paths:
        rank, _reasons = candidates[path]
        result_items.append(ImpactCandidate(path, rank, tuple(allocated[path])))
    if reasons_truncated:
        warnings.add("reasons truncated for one or more candidates")

    return ImpactReport(tuple(result_items), warnings.result())
