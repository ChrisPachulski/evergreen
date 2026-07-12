"""Validation for passive, candidate-only provider evidence."""

import json
import math
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping

MAX_FILE_BYTES = 4 * 1024 * 1024
MAX_RECORDS = 10_000
MAX_METADATA_BYTES = 16 * 1024
MAX_METADATA_DEPTH = 8
MAX_PATH_BYTES = 4096
MAX_VALUE_BYTES = 8192
FIELDS = {
    "provider", "version", "type", "path", "line", "span", "symbol",
    "old", "current", "confidence", "metadata",
}
REQUIRED_FIELDS = FIELDS - {"metadata"}
VERDICT_FIELDS = {"verdict", "finding", "drift", "status"}


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


@dataclass(frozen=True)
class Evidence:
    provider: str
    version: str
    type: str
    path: str
    line: int
    span: int | None
    symbol: str | None
    old: str | None
    current: str | None
    confidence: str
    metadata: Mapping[str, Any]


def _text(value, name, *, nullable=False, limit=MAX_VALUE_BYTES):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > limit:
        raise ValueError(f"{name} must be a bounded non-empty string")
    return value


def _path(value, repo):
    value = _text(value, "path", limit=MAX_PATH_BYTES)
    pure = PurePosixPath(value)
    if ("\\" in value or any(ord(character) < 32 or ord(character) == 127 for character in value) or
            pure.is_absolute() or value != pure.as_posix() or
            any(part in ("", ".", "..") for part in pure.parts)):
        raise ValueError("path must be normalized and repository-relative")
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
        raise ValueError("path cannot be resolved") from None
    if resolved != root and root not in resolved.parents:
        raise ValueError("path escapes repository")
    return value


def _metadata(value):
    if not isinstance(value, dict):
        raise ValueError("metadata must be an object")
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as error:
        raise ValueError("metadata must contain bounded JSON values") from error
    if len(encoded) > MAX_METADATA_BYTES:
        raise ValueError("metadata exceeds byte limit")
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_METADATA_DEPTH:
            raise ValueError("metadata exceeds depth limit")
        if isinstance(current, dict):
            if any(not isinstance(key, str) for key in current):
                raise ValueError("metadata keys must be strings")
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)
        elif isinstance(current, float) and not math.isfinite(current):
            raise ValueError("metadata numbers must be finite")
        elif current is not None and not isinstance(current, (str, int, float, bool)):
            raise ValueError("metadata must contain JSON values")
    return value, encoded.decode("utf-8")


def _freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(value[key]) for key in sorted(value)})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _record(value, repo):
    duplicate = _duplicate_key(value)
    if duplicate is not None:
        raise ValueError(f"duplicate JSON key: {duplicate}")
    if isinstance(value, dict) and VERDICT_FIELDS & set(value):
        raise ValueError("candidate-only evidence cannot contain verdict fields")
    if (not isinstance(value, dict) or not REQUIRED_FIELDS <= set(value) or
            not set(value) <= FIELDS):
        raise ValueError("record must be an object with exactly the version 1 fields")
    line = value["line"]
    if type(line) is not int or not 1 <= line <= 2_147_483_647:
        raise ValueError("line must be a positive integer")
    span = value["span"]
    if span is not None and (type(span) is not int or not 1 <= span <= 2_147_483_647):
        raise ValueError("span must be null or a positive integer")
    confidence = value["confidence"]
    if confidence not in ("deterministic", "advisory"):
        raise ValueError("confidence must be deterministic or advisory")
    metadata, metadata_key = _metadata(value.get("metadata", {}))
    evidence = Evidence(
        provider=_text(value["provider"], "provider", limit=256),
        version=_text(value["version"], "version", limit=256),
        type=_text(value["type"], "type", limit=256),
        path=_path(value["path"], repo),
        line=line,
        span=span,
        symbol=_text(value["symbol"], "symbol", nullable=True),
        old=_text(value["old"], "old", nullable=True),
        current=_text(value["current"], "current", nullable=True),
        confidence=confidence,
        metadata=_freeze(metadata),
    )
    key = (
        evidence.provider, evidence.version, evidence.type, evidence.path, evidence.line,
        evidence.span or 0, evidence.symbol or "", evidence.old or "", evidence.current or "",
        evidence.confidence, metadata_key,
    )
    return evidence, key


def load_evidence(path: Path, repo: Path) -> tuple[list[Evidence], list[str]]:
    """Load valid passive facts, returning warnings for every rejected input."""
    path, repo = Path(path), Path(repo)
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            return [], ["evidence input must be a regular file"]
        flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            os.close(descriptor)
            return [], ["evidence input must be a regular file"]
        if info.st_size > MAX_FILE_BYTES:
            os.close(descriptor)
            return [], [f"evidence file too large (maximum {MAX_FILE_BYTES} bytes)"]
        with os.fdopen(descriptor, "rb") as source:
            payload = source.read(MAX_FILE_BYTES + 1)
        if len(payload) > MAX_FILE_BYTES:
            return [], [f"evidence file too large (maximum {MAX_FILE_BYTES} bytes)"]
        values = json.loads(payload, object_pairs_hook=_decoded_object)
    except (OSError, UnicodeError) as error:
        return [], [f"could not read evidence file: {error}"]
    except (json.JSONDecodeError, RecursionError):
        return [], ["evidence file contains invalid JSON"]
    if isinstance(values, _DecodedObject) and values.duplicate_keys:
        return [], [f"evidence file contains duplicate JSON key: {values.duplicate_keys[0]}"]
    if not isinstance(values, list):
        return [], ["evidence file root must be an array"]
    if len(values) > MAX_RECORDS:
        return [], [f"evidence file has too many records (maximum {MAX_RECORDS})"]

    accepted = {}
    warnings = []
    for index, value in enumerate(values, 1):
        try:
            evidence, key = _record(value, repo)
        except (ValueError, RecursionError) as error:
            warnings.append(f"record {index}: {error}")
            continue
        if key in accepted:
            warnings.append(f"record {index}: duplicate evidence ignored")
            continue
        accepted[key] = evidence
    return [accepted[key] for key in sorted(accepted)], warnings
