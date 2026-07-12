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

DECLARATION_RE = re.compile(
    rb"(?m)^[ \t]*(?:export[ \t]+)?"
    rb"(?:public[ \t]+|pub(?:\([^\r\n)]*\))?[ \t]+)?(?:async[ \t]+)?"
    rb"(?:class|struct|enum|protocol|interface|type|def|func|fn|function)[ \t]+"
    rb"([A-Za-z_][A-Za-z0-9_]*)"
)
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
        scanned_bytes += len(payload)
        symbols.update(match.decode("ascii") for match in DECLARATION_RE.findall(payload))
        if len(symbols) >= MAX_CONTRACT_SYMBOLS:
            break
    return sorted(symbols)[:max(MAX_CONTRACT_SYMBOLS, 0)], truncated


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
        except (Exception, RecursionError) as error:
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
        for source in changed_paths:
            if not spend_work():
                break
            if source.encode("utf-8") in payload:
                add(doc, 80, f"living doc mentions changed path {source}")
        if work_truncated:
            break
        for symbol in symbols:
            if not spend_work():
                break
            encoded = symbol.encode("utf-8")
            if re.search(rb"(?<![A-Za-z0-9_])" + re.escape(encoded) +
                         rb"(?![A-Za-z0-9_])", payload):
                add(doc, 80, f"living doc mentions contract symbol {symbol}")
        if work_truncated:
            break
    if work_truncated:
        warnings.add(f"matching work truncated (maximum {MAX_MATCH_WORK} operations)")

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
