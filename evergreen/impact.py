"""Additive candidate discovery from passive evidence and source-to-doc maps."""

import fnmatch
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .evidence import Evidence

MAX_FILE_BYTES = 1024 * 1024
MAX_MAPS = 1000
MAX_PATTERNS = 100
MAX_DOCS = 100
MAX_STRING_BYTES = 4096


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


class _DuplicateKey(ValueError):
    pass


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _DuplicateKey(key)
        value[key] = item
    return value


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
    value, _pure = _lexical(value, "source pattern")
    index = 0
    while index < len(value):
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
        if end < 0 or end == start + 1:
            raise ValueError("source pattern has invalid brackets")
        index = end + 1
    return value


def _map_record(value, repo):
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
        root = json.loads(payload, object_pairs_hook=_unique_object)
    except _DuplicateKey as error:
        return [], [f"map config contains duplicate JSON key: {error}"]
    except (OSError, UnicodeError) as error:
        return [], [f"could not read map config: {error}"]
    except (json.JSONDecodeError, RecursionError):
        return [], ["map config contains invalid JSON"]
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


def impact(repo: Path, paths: list[str], evidence: list[Evidence]) -> ImpactReport:
    """Return additive candidates only; mappings never suppress or decide drift."""
    repo = Path(repo)
    mappings, warnings = load_map(repo)
    candidates = {}

    def add(path, rank, reason):
        current_rank, reasons = candidates.get(path, (0, set()))
        reasons.add(reason)
        candidates[path] = (max(current_rank, rank), reasons)

    sources = set()
    for raw_path in paths:
        try:
            path = _path(raw_path, repo, "changed path")
        except (TypeError, ValueError) as error:
            warnings.append(f"changed path: {error}")
            continue
        sources.add(path)
        add(path, 10, f"changed path {path}")
    for item in evidence:
        if not isinstance(item, Evidence):
            warnings.append("provider evidence item has invalid type")
            continue
        try:
            path = _path(item.path, repo, "evidence path")
        except (TypeError, ValueError) as error:
            warnings.append(f"evidence path: {error}")
            continue
        sources.add(path)
        reason = f"evidence {item.provider}@{item.version} {item.type} ({item.confidence})"
        add(path, 50 if item.confidence == "deterministic" else 40, reason)
    for source in sorted(sources):
        for mapping in mappings:
            for pattern in mapping.sources:
                if fnmatch.fnmatchcase(source, pattern):
                    reason = f"map {pattern} matched {source}"
                    for doc in mapping.docs:
                        add(doc, 100, reason)
    result = tuple(
        ImpactCandidate(path, rank, tuple(sorted(reasons)))
        for path, (rank, reasons) in sorted(
            candidates.items(), key=lambda item: (-item[1][0], item[0].lower(), item[0])
        )
    )
    return ImpactReport(result, tuple(sorted(warnings)))
