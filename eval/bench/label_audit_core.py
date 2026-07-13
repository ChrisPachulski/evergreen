"""Human-label audit primitives. This module never calls a model or network."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from eval.bench.artifact import read_bytes


LANGUAGES = ("java", "python", "typescript", "rust", "go")
MAX_AUDIT_INPUT_BYTES = 64 * 1024 * 1024
_ARCHIVE_NAME = re.compile(r"\.rows-(\d+)\.([0-9a-f]{64})\.json$")


def canonical_language(value: str) -> str:
    language = value.casefold() if isinstance(value, str) else ""
    language = "typescript" if language == "ts" else language
    if language not in LANGUAGES:
        raise ValueError(f"unsupported audit language: {value!r}")
    return language


@dataclass(frozen=True)
class AuditItem:
    id: str
    language: str
    code: str
    doc: str
    func: str
    label: str
    category: str | None
    final_status: str
    final_verdict: str | None
    artifact_sha256: str

    @property
    def key(self) -> tuple[str, str]:
        return self.language, self.id


@dataclass(frozen=True)
class ArtifactInput:
    path: Path
    sha256: str
    language: str
    row_count: int
    dataset_sha256: str
    items: tuple[AuditItem, ...]


@dataclass(frozen=True)
class SourcePool:
    path: Path
    sha256: str
    language: str
    row_count: int
    provenance_status: str
    rows: tuple[dict, ...]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(read_bytes(path, MAX_AUDIT_INPUT_BYTES, label="label audit input")).hexdigest()


def _result(row: dict) -> tuple[str, str | None]:
    got = row.get("got")
    if not isinstance(got, dict):
        raise ValueError("benchmark row result must be an object")
    status = got.get("final_status")
    verdict = got.get("final_verdict")
    if status is None and got.get("verdict") in ("consistent", "inconsistent"):
        return "complete", got["verdict"]
    if status == "complete" and verdict in ("consistent", "inconsistent"):
        return status, verdict
    if status == "abstain" and verdict is None:
        return status, None
    if status == "complete":
        raise ValueError("completed benchmark result requires a verdict")
    if status == "abstain":
        raise ValueError("abstain benchmark result cannot carry a verdict")
    raise ValueError("benchmark result status is invalid")


def load_artifact(path: Path) -> ArtifactInput:
    path = Path(path)
    raw = read_bytes(path, MAX_AUDIT_INPUT_BYTES, label="label audit artifact")
    digest = hashlib.sha256(raw).hexdigest()
    document = json.loads(raw)
    rows = document.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("benchmark artifact rows must be a non-empty list")
    match = _ARCHIVE_NAME.search(path.name)
    if match and (int(match.group(1)) != len(rows) or match.group(2) != digest):
        raise ValueError("benchmark archive filename does not match row count and digest")
    items = []
    seen = set()
    for row in rows:
        if row.get("label") not in ("consistent", "inconsistent"):
            raise ValueError("benchmark row label is invalid")
        language = canonical_language(row.get("language", ""))
        identifier = row.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError("benchmark row id is invalid")
        key = language, identifier
        if key in seen:
            raise ValueError(f"duplicate benchmark row: {identifier}")
        seen.add(key)
        status, verdict = _result(row)
        for field in ("code", "doc", "func"):
            if not isinstance(row.get(field), str):
                raise ValueError(f"benchmark row {field} is invalid")
        items.append(AuditItem(identifier, language, row["code"], row["doc"], row["func"],
                               row["label"], row.get("category"), status, verdict, digest))
    languages = {item.language for item in items}
    if len(languages) != 1:
        raise ValueError("benchmark artifact must contain exactly one language")
    dataset = document.get("metadata", {}).get("dataset", {})
    dataset_sha = dataset.get("sha256")
    if not isinstance(dataset_sha, str) or not re.fullmatch(r"[0-9a-f]{64}", dataset_sha):
        raise ValueError("benchmark dataset SHA-256 is invalid")
    return ArtifactInput(path, digest, next(iter(languages)), len(items), dataset_sha, tuple(items))


def load_source_pool(path: Path, language: str) -> SourcePool:
    path = Path(path)
    raw = read_bytes(path, MAX_AUDIT_INPUT_BYTES, label="label source pool")
    parsed = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if not parsed:
        raise ValueError("source pool is empty")
    language = canonical_language(language)
    rows = []
    complete = True
    for row in parsed:
        if canonical_language(row.get("language", "")) != language:
            raise ValueError("source pool language mismatch")
        source = row.get("source")
        source_complete = isinstance(source, dict) and all(
            isinstance(source.get(key), str) and source[key] for key in ("owner", "project", "file", "commit")
        )
        copy = dict(row)
        copy["source_status"] = "complete" if source_complete else "missing"
        rows.append(copy)
        complete &= source_complete
    return SourcePool(path, hashlib.sha256(raw).hexdigest(), language, len(rows),
                      "complete" if complete else "unverified", tuple(rows))
