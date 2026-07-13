"""Human-label audit primitives. This module never calls a model or network."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import random
import re
from dataclasses import dataclass
from pathlib import Path

try:
    from .artifact import read_bytes
except ImportError:  # Direct script execution through label_audit.py.
    from artifact import read_bytes


LANGUAGES = ("java", "python", "typescript", "rust", "go")
SOURCE_MANIFEST_RELATIVE = Path("eval/bench/human-audit/source-pools.json")
SPLIT_ROW_TOLERANCE = 0.05
SPLIT_CELL_TOLERANCE = 0.15
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


@dataclass(frozen=True)
class SourceManifestEntry:
    language: str
    status: str
    path: Path | None
    sha256: str | None
    row_count: int | None


@dataclass(frozen=True)
class SourceManifest:
    path: Path
    sha256: str
    entries: tuple[SourceManifestEntry, ...]

    def by_language(self) -> dict[str, SourceManifestEntry]:
        return {entry.language: entry for entry in self.entries}


@dataclass(frozen=True)
class SelectedItem:
    item: AuditItem
    stratum: str
    inclusion_probability: float
    source_kind: str


@dataclass(frozen=True)
class SampleSelection:
    audit_id: str
    seed: int
    selected: tuple[SelectedItem, ...]
    missing_discarded_languages: tuple[str, ...]
    input_hashes: tuple[tuple[str, str], ...]

    def count(self, stratum: str) -> int:
        return sum(row.stratum == stratum for row in self.selected)


@dataclass(frozen=True)
class PacketOutputs:
    annotator_a: Path
    annotator_b: Path
    adjudicator_source: Path
    coordinator: Path
    blind_key: Path


@dataclass(frozen=True)
class AnnotationSet:
    audit_id: str
    rubric_sha256: str
    annotator_id: str
    trust_status: str
    humanity_verified: bool
    judgments: tuple[dict, ...]
    coordinator_sha256: str = ""
    packet_sha256: str = ""


@dataclass(frozen=True)
class CombinedLabel:
    blind_id: str
    final_verdict: str | None
    final_category: str | None
    unresolved: bool
    review_reason: str | None
    submitted_judgments: tuple[dict, ...]


@dataclass(frozen=True)
class CombinedLabels:
    audit_id: str
    rubric_sha256: str
    labels: tuple[CombinedLabel, ...]


@dataclass(frozen=True)
class SplitResult:
    development_ids: tuple[str, ...]
    holdout_ids: tuple[str, ...]
    development_repositories: tuple[str, ...]
    holdout_repositories: tuple[str, ...]


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


def load_source_manifest(path: Path, repo: Path) -> SourceManifest:
    path, repo = Path(path), Path(repo).resolve()
    authoritative = (repo / SOURCE_MANIFEST_RELATIVE).resolve()
    if path.resolve() != authoritative:
        raise ValueError("source pool manifest must be the authoritative tracked repository manifest")
    raw = read_bytes(path, MAX_AUDIT_INPUT_BYTES, label="source pool manifest")
    document = json.loads(raw)
    if not isinstance(document, dict) or set(document) != {"schema_version", "pools"}:
        raise ValueError("source pool manifest fields are invalid")
    if document["schema_version"] != 1 or not isinstance(document["pools"], list):
        raise ValueError("source pool manifest schema is invalid")
    entries = []
    seen = set()
    available_keys = {"language", "status", "row_count", "sha256", "path"}
    missing_keys = {"language", "status", "expected_row_count", "retained_row_count",
                    "missing_discarded_count"}
    for row in document["pools"]:
        if not isinstance(row, dict) or row.get("status") not in ("available", "missing"):
            raise ValueError("source pool manifest entry is invalid")
        expected = available_keys if row["status"] == "available" else missing_keys
        if set(row) != expected:
            raise ValueError("source pool manifest entry fields are invalid")
        language = canonical_language(row.get("language", ""))
        if language in seen:
            raise ValueError("source pool manifest languages must be unique")
        seen.add(language)
        if row["status"] == "missing":
            counts = [row[name] for name in ("expected_row_count", "retained_row_count",
                                             "missing_discarded_count")]
            if any(not isinstance(value, int) or value < 0 for value in counts):
                raise ValueError("source pool manifest counts are invalid")
            if counts[0] - counts[1] != counts[2]:
                raise ValueError("source pool manifest missing count is inconsistent")
            entries.append(SourceManifestEntry(language, "missing", None, None, None))
            continue
        digest, count, relative = row["sha256"], row["row_count"], row["path"]
        if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) or
                not isinstance(count, int) or count <= 0 or
                not isinstance(relative, str) or not relative):
            raise ValueError("source pool manifest available entry is invalid")
        resolved = (repo / relative).resolve()
        if resolved == repo or repo not in resolved.parents:
            raise ValueError("source pool manifest path escapes repository")
        actual = read_bytes(resolved, MAX_AUDIT_INPUT_BYTES, label="manifest source pool")
        if hashlib.sha256(actual).hexdigest() != digest:
            raise ValueError(f"source pool manifest hash mismatch for {language}")
        if len([line for line in actual.splitlines() if line.strip()]) != count:
            raise ValueError(f"source pool manifest row count mismatch for {language}")
        entries.append(SourceManifestEntry(language, "available", resolved, digest, count))
    if seen != set(LANGUAGES):
        raise ValueError("source pool manifest must declare every language")
    return SourceManifest(path, hashlib.sha256(raw).hexdigest(), tuple(entries))


def _source_item(row: dict, language: str, digest: str) -> AuditItem:
    for field in ("id", "code", "doc", "func", "label"):
        if not isinstance(row.get(field), str) or not row[field]:
            raise ValueError(f"source pool row {field} is invalid")
    return AuditItem(row["id"], language, row["code"], row["doc"], row["func"],
                     row["label"], row.get("category"), "unscored", None, digest)


def build_sample(artifacts: tuple[ArtifactInput, ...], source_pools: dict[str, SourcePool], *,
                 audit_id: str, seed: int = 20260713, tn_per_language: int = 25,
                 discarded_per_language: int = 20,
                 source_manifest: SourceManifest | None = None) -> SampleSelection:
    by_language = {artifact.language: artifact for artifact in artifacts}
    if len(by_language) != len(artifacts) or set(by_language) != set(LANGUAGES):
        raise ValueError("audit requires exactly one artifact for every declared language")
    selected = []
    seen = set()
    for language in LANGUAGES:
        artifact = by_language[language]
        tn = []
        for item in sorted(artifact.items, key=lambda value: value.key):
            if item.key in seen:
                raise ValueError(f"duplicate audit item: {item.key}")
            seen.add(item.key)
            if item.label == "inconsistent":
                selected.append(SelectedItem(item, "nominal_positive", 1.0, "retained"))
            elif item.final_status == "complete" and item.final_verdict == "inconsistent":
                selected.append(SelectedItem(item, "nominal_false_positive", 1.0, "retained"))
            elif item.final_status != "complete":
                selected.append(SelectedItem(item, "abstention", 1.0, "retained"))
            else:
                tn.append(item)
        if len(tn) < tn_per_language:
            raise ValueError(f"{language} has fewer than {tn_per_language} true-negative candidates")
        rng = random.Random(f"{seed}:{language}")
        for item in rng.sample(tn, tn_per_language):
            selected.append(SelectedItem(item, "true_negative_sample",
                                         tn_per_language / len(tn), "retained"))

    if source_pools and source_manifest is None:
        raise ValueError("source pool manifest admission is required")
    manifest = source_manifest.by_language() if source_manifest else {}
    for language, pool in source_pools.items():
        entry = manifest.get(language)
        if (entry is None or entry.status != "available" or entry.path is None or
                pool.path.resolve() != entry.path or pool.sha256 != entry.sha256 or
                pool.row_count != entry.row_count):
            raise ValueError(f"source pool does not match manifest admission: {language}")
    missing = []
    for language in ("python", "typescript", "rust", "go"):
        pool = source_pools.get(language)
        entry = manifest.get(language)
        if pool is None or entry is None or entry.status != "available":
            missing.append(language)
            continue
        if pool.language != language:
            raise ValueError("source pool language mismatch")
        retained = {item.id for item in by_language[language].items}
        discarded = [_source_item(row, language, pool.sha256) for row in pool.rows
                     if row.get("id") not in retained]
        each = discarded_per_language // 2
        sides = {
            "old": [item for item in discarded if item.id.endswith("-old")],
            "new": [item for item in discarded if item.id.endswith("-new")],
        }
        for side, candidates in sides.items():
            if len(candidates) < each:
                raise ValueError(f"{language} discarded {side} pool has fewer than {each} rows")
            rng = random.Random(f"{seed}:{language}:discarded:{side}")
            for item in rng.sample(sorted(candidates, key=lambda value: value.key), each):
                selected.append(SelectedItem(item, "discarded_candidate", each / len(candidates),
                                             "discarded"))
    selected.sort(key=lambda value: (value.item.language, value.item.id, value.stratum))
    extra_hashes = [(f"{language}-source", pool.sha256)
                    for language, pool in source_pools.items()]
    if source_manifest is not None:
        extra_hashes.append(("source-manifest", source_manifest.sha256))
    hashes = tuple(sorted((artifact.language, artifact.sha256) for artifact in artifacts) +
                   sorted(extra_hashes))
    return SampleSelection(audit_id, seed, tuple(selected), tuple(sorted(missing)), hashes)


def blind_id(key: bytes, audit_id: str, item: AuditItem) -> str:
    if len(key) < 32:
        raise ValueError("blind key must be at least 32 bytes")
    message = "\0".join((audit_id, item.language, item.id)).encode()
    return "item-" + hmac.new(key, message, hashlib.sha256).hexdigest()[:24]


def _numbered(value: str) -> str:
    return "\n".join(f"{number} | {line}" for number, line in enumerate(value.splitlines(), 1))


def document_identity(value: dict, field: str) -> str:
    unsigned = {key: item for key, item in value.items() if key != field}
    encoded = json.dumps(unsigned, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _atomic_json(path: Path, value: object, mode: int = 0o600) -> None:
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing output: {path}")
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _external_destination(path: Path, repo: Path) -> Path:
    path, repo = Path(path), Path(repo).resolve()
    if not path.is_absolute():
        raise ValueError("output must be an absolute path")
    parent = path.parent.resolve()
    if parent == repo or repo in parent.parents:
        raise ValueError("output must live outside the repository")
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing output: {path}")
    return path


def write_private_json(path: Path, value: object, *, repo: Path) -> Path:
    path = _external_destination(path, repo)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _atomic_json(path, value)
    os.chmod(path, 0o600)
    return path


def write_private_text(path: Path, value: str, *, repo: Path) -> Path:
    path = _external_destination(path, repo)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path, follow_symlinks=False)
        temporary.unlink()
        os.chmod(path, 0o600)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return path


def write_blinded_packets(selection: SampleSelection, work_dir: Path, *, blind_key: bytes,
                          rubric_sha256: str, repo: Path) -> PacketOutputs:
    work_dir, repo = Path(work_dir), Path(repo).resolve()
    if not work_dir.is_absolute():
        raise ValueError("audit work directory must be absolute")
    resolved_parent = work_dir.parent.resolve()
    if resolved_parent == repo or repo in resolved_parent.parents:
        raise ValueError("audit work directory must live outside the repository")
    if len(blind_key) < 32:
        raise ValueError("blind key must be at least 32 bytes")
    if not re.fullmatch(r"[0-9a-f]{64}", rubric_sha256):
        raise ValueError("rubric SHA-256 is invalid")
    work_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(work_dir, 0o700)
    mapped = []
    for selected in selection.selected:
        opaque = blind_id(blind_key, selection.audit_id, selected.item)
        mapped.append((opaque, selected))
    base = {"schema_version": 1, "audit_id": selection.audit_id,
            "rubric_sha256": rubric_sha256}
    packet_items = {
        opaque: {"blind_id": opaque, "language": selected.item.language,
                 "code": _numbered(selected.item.code),
                 "documentation": _numbered(selected.item.doc)}
        for opaque, selected in mapped
    }
    def ordered(role: str) -> list[dict]:
        return [packet_items[key] for key in sorted(packet_items, key=lambda value:
                hmac.new(blind_key, f"{role}:{value}".encode(), hashlib.sha256).digest())]
    a, b, source = (work_dir / "annotator-a.packet.json",
                    work_dir / "annotator-b.packet.json",
                    work_dir / "adjudicator-source.packet.json")
    coordinator = work_dir / "coordinator.json"
    key_path = work_dir / "blind.key"
    coordinator_document = {**base, "missing_discarded_languages":
                            list(selection.missing_discarded_languages),
                            "input_hashes": dict(selection.input_hashes),
                            "items": [{"blind_id": opaque, "id": selected.item.id,
                                       "language": selected.item.language,
                                       "label": selected.item.label,
                                       "category": selected.item.category,
                                       "final_status": selected.item.final_status,
                                       "final_verdict": selected.item.final_verdict,
                                       "stratum": selected.stratum,
                                       "inclusion_probability": selected.inclusion_probability,
                                       "source_kind": selected.source_kind,
                                       "artifact_sha256": selected.item.artifact_sha256,
                                       "code": selected.item.code, "doc": selected.item.doc,
                                       "func": selected.item.func}
                                      for opaque, selected in mapped]}
    coordinator_document["coordinator_sha256"] = document_identity(
        coordinator_document, "coordinator_sha256")
    _atomic_json(coordinator, coordinator_document)
    for path, role in ((a, "annotator-a"), (b, "annotator-b"),
                       (source, "adjudicator")):
        packet = {**base, "coordinator_sha256": coordinator_document["coordinator_sha256"],
                  "items": ordered(role)}
        packet["packet_sha256"] = document_identity(packet, "packet_sha256")
        _atomic_json(path, packet)
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(blind_key)
    return PacketOutputs(a, b, source, coordinator, key_path)


_PACKET_ITEM_KEYS = {"blind_id", "language", "code", "documentation"}
_COORDINATOR_ITEM_KEYS = {"blind_id", "id", "language", "label", "category",
                          "final_status", "final_verdict", "stratum",
                          "inclusion_probability", "source_kind", "artifact_sha256",
                          "code", "doc", "func"}


def load_packet(path: Path) -> dict:
    document = json.loads(read_bytes(Path(path), MAX_AUDIT_INPUT_BYTES,
                                     label="annotation packet"))
    expected = {"schema_version", "audit_id", "rubric_sha256", "coordinator_sha256",
                "packet_sha256", "items"}
    if not isinstance(document, dict) or set(document) != expected:
        raise ValueError("annotation packet fields are invalid")
    if document.get("schema_version") != 1:
        raise ValueError("annotation packet schema is invalid")
    for field in ("rubric_sha256", "coordinator_sha256", "packet_sha256"):
        if not isinstance(document.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", document[field]):
            raise ValueError(f"annotation packet {field} is invalid")
    if document["packet_sha256"] != document_identity(document, "packet_sha256"):
        raise ValueError("annotation packet identity mismatch")
    if not isinstance(document.get("audit_id"), str) or not document["audit_id"]:
        raise ValueError("annotation packet audit_id is invalid")
    items = document.get("items")
    if not isinstance(items, list) or any(not isinstance(row, dict) or set(row) != _PACKET_ITEM_KEYS
                                          for row in items):
        raise ValueError("annotation packet items are invalid")
    identifiers = [row.get("blind_id") for row in items]
    if (any(not isinstance(value, str) or not value for value in identifiers) or
            len(identifiers) != len(set(identifiers))):
        raise ValueError("annotation packet blind IDs must be unique")
    for row in items:
        canonical_language(row.get("language", ""))
        if any(not isinstance(row[field], str) for field in ("code", "documentation")):
            raise ValueError("annotation packet content is invalid")
    return document


def load_coordinator(path: Path) -> dict:
    document = json.loads(read_bytes(Path(path), MAX_AUDIT_INPUT_BYTES,
                                     label="audit coordinator"))
    expected = {"schema_version", "audit_id", "rubric_sha256", "coordinator_sha256",
                "missing_discarded_languages", "input_hashes", "items"}
    if not isinstance(document, dict) or set(document) != expected:
        raise ValueError("coordinator fields are invalid")
    if document.get("schema_version") != 1:
        raise ValueError("coordinator schema is invalid")
    if not isinstance(document.get("audit_id"), str) or not document["audit_id"]:
        raise ValueError("coordinator audit_id is invalid")
    for field in ("rubric_sha256", "coordinator_sha256"):
        if not isinstance(document.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", document[field]):
            raise ValueError(f"coordinator {field} is invalid")
    if document["coordinator_sha256"] != document_identity(document, "coordinator_sha256"):
        raise ValueError("coordinator identity mismatch")
    hashes = document.get("input_hashes")
    if (not isinstance(hashes, dict) or any(not isinstance(key, str) or
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for key, value in hashes.items())):
        raise ValueError("coordinator input hashes are invalid")
    required_hashes = set(LANGUAGES)
    allowed_hashes = required_hashes | {f"{language}-source" for language in LANGUAGES} | {
        "source-manifest"}
    if not required_hashes <= set(hashes) or not set(hashes) <= allowed_hashes:
        raise ValueError("coordinator input hash inventory is invalid")
    missing = document.get("missing_discarded_languages")
    expected_missing = sorted(language for language in ("python", "typescript", "rust", "go")
                              if f"{language}-source" not in hashes)
    if missing != expected_missing:
        raise ValueError("coordinator missing source pools are inconsistent")
    items = document.get("items")
    if not isinstance(items, list) or not items or any(
            not isinstance(row, dict) or set(row) != _COORDINATOR_ITEM_KEYS
                                          for row in items):
        raise ValueError("coordinator items are invalid")
    blind_ids, item_keys = [], []
    for row in items:
        language = canonical_language(row.get("language", ""))
        blind_ids.append(row.get("blind_id")); item_keys.append((language, row.get("id")))
        if (not isinstance(row.get("blind_id"), str) or not row["blind_id"] or
                not isinstance(row.get("id"), str) or not row["id"]):
            raise ValueError("coordinator item identity is invalid")
        if (not isinstance(row.get("inclusion_probability"), (int, float)) or
                isinstance(row.get("inclusion_probability"), bool) or
                not 0 < row["inclusion_probability"] <= 1):
            raise ValueError("coordinator inclusion probability is invalid")
        if row.get("stratum") not in ("nominal_positive", "nominal_false_positive",
                                       "abstention", "true_negative_sample",
                                       "discarded_candidate"):
            raise ValueError("coordinator stratum is invalid")
        if row.get("source_kind") not in ("retained", "discarded"):
            raise ValueError("coordinator source kind is invalid")
        if ((row["stratum"] == "discarded_candidate") !=
                (row["source_kind"] == "discarded")):
            raise ValueError("coordinator source kind and stratum are inconsistent")
        if row.get("label") not in ("consistent", "inconsistent"):
            raise ValueError("coordinator nominal label is invalid")
        if row.get("category") not in (None, "direct-mismatch", "over-promise"):
            raise ValueError("coordinator category is invalid")
        if row.get("final_status") not in ("complete", "abstain", "unscored"):
            raise ValueError("coordinator final status is invalid")
        if ((row["final_status"] == "complete" and
             row.get("final_verdict") not in ("consistent", "inconsistent")) or
                (row["final_status"] != "complete" and row.get("final_verdict") is not None)):
            raise ValueError("coordinator final verdict is invalid")
        if any(not isinstance(row.get(field), str) for field in ("code", "doc", "func")):
            raise ValueError("coordinator item content is invalid")
        if not isinstance(row.get("artifact_sha256"), str) or not re.fullmatch(
                r"[0-9a-f]{64}", row["artifact_sha256"]):
            raise ValueError("coordinator artifact hash is invalid")
        hash_key = f"{language}-source" if row["source_kind"] == "discarded" else language
        if row["artifact_sha256"] != hashes.get(hash_key):
            raise ValueError("coordinator item does not match declared input hash")
    if len(blind_ids) != len(set(blind_ids)) or len(item_keys) != len(set(item_keys)):
        raise ValueError("coordinator item identities must be unique")
    return document


_JUDGMENT_KEYS = {"blind_id", "verdict", "category", "documentation_claim",
                  "code_evidence", "rationale", "missing_context"}


def validate_judgment(judgment: dict) -> None:
    if set(judgment) != _JUDGMENT_KEYS:
        raise ValueError("judgment fields are invalid")
    verdict = judgment.get("verdict")
    if verdict not in ("consistent", "inconsistent", "insufficient-context"):
        raise ValueError("judgment verdict is invalid")
    if not isinstance(judgment.get("blind_id"), str) or not judgment["blind_id"]:
        raise ValueError("blind_id is invalid")
    for field in ("documentation_claim", "code_evidence", "rationale"):
        if not isinstance(judgment.get(field), str) or not judgment[field].strip():
            raise ValueError(f"{field} is required")
    category = judgment.get("category")
    if verdict == "inconsistent" and category not in ("direct-mismatch", "over-promise"):
        raise ValueError("category is required for inconsistent judgment")
    if verdict != "inconsistent" and category is not None:
        raise ValueError("category must be null unless inconsistent")
    missing = judgment.get("missing_context")
    if verdict == "insufficient-context" and (not isinstance(missing, str) or not missing.strip()):
        raise ValueError("missing_context is required")
    if verdict != "insufficient-context" and missing is not None:
        raise ValueError("missing_context must be null unless context is insufficient")


def _judgments_by_id(annotations: AnnotationSet) -> dict[str, dict]:
    return {row["blind_id"]: row for row in annotations.judgments}


def select_third_review(first: AnnotationSet, second: AnnotationSet, *, rate: float = 0.10,
                        seed: int = 20260713) -> tuple[str, ...]:
    if first.audit_id != second.audit_id or first.rubric_sha256 != second.rubric_sha256:
        raise ValueError("annotation sets target different audits")
    if first.annotator_id == second.annotator_id:
        raise ValueError("initial annotators must be different")
    a, b = _judgments_by_id(first), _judgments_by_id(second)
    if set(a) != set(b):
        raise ValueError("annotation sets cover different packet items")
    mandatory, agreements = [], []
    for identifier in sorted(a):
        if ((a[identifier]["verdict"], a[identifier]["category"]) !=
                (b[identifier]["verdict"], b[identifier]["category"]) or
                "insufficient-context" in (a[identifier]["verdict"], b[identifier]["verdict"])):
            mandatory.append(identifier)
        else:
            agreements.append(identifier)
    rng = random.Random(seed)
    qa = rng.sample(agreements, math.ceil(rate * len(agreements))) if agreements else []
    return tuple(sorted(set(mandatory + qa)))


def load_annotations(path: Path, packet: Path) -> AnnotationSet:
    document = json.loads(read_bytes(Path(path), MAX_AUDIT_INPUT_BYTES,
                                     label="human annotations"))
    packet_document = load_packet(packet)
    expected_fields = {"schema_version", "audit_id", "rubric_sha256", "coordinator_sha256",
                       "packet_sha256", "annotator", "judgments"}
    if set(document) != expected_fields:
        raise ValueError("annotation fields are invalid")
    if document.get("schema_version") != 1:
        raise ValueError("annotation schema_version must be 1")
    if any(document.get(field) != packet_document.get(field)
           for field in ("audit_id", "rubric_sha256", "coordinator_sha256", "packet_sha256")):
        raise ValueError("annotations do not match packet identity")
    annotator = document.get("annotator")
    expected_annotator = {"annotator_id", "human_judgment", "worked_independently",
                          "used_model_assistance"}
    if not isinstance(annotator, dict) or set(annotator) != expected_annotator:
        raise ValueError("annotator attestation is invalid")
    if (not isinstance(annotator["annotator_id"], str) or not annotator["annotator_id"] or
            annotator["human_judgment"] is not True or
            annotator["worked_independently"] is not True or
            annotator["used_model_assistance"] is not False):
        raise ValueError("human independent model-free attestation is required")
    judgments = document.get("judgments")
    if not isinstance(judgments, list):
        raise ValueError("judgments must be an array")
    for judgment in judgments:
        if not isinstance(judgment, dict):
            raise ValueError("judgment must be an object")
        validate_judgment(judgment)
    expected = {row.get("blind_id") for row in packet_document.get("items", [])}
    actual = [row["blind_id"] for row in judgments]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("annotation coverage must exactly match packet")
    return AnnotationSet(document["audit_id"], document["rubric_sha256"],
                         annotator["annotator_id"], "self-attested-human", False,
                         tuple(judgments), document["coordinator_sha256"],
                         document["packet_sha256"])


def write_third_packet(source_packet: Path, blind_ids: tuple[str, ...], destination: Path, *,
                       repo: Path) -> Path:
    source = load_packet(source_packet)
    wanted = set(blind_ids)
    items = [row for row in source.get("items", []) if row.get("blind_id") in wanted]
    if {row["blind_id"] for row in items} != wanted:
        raise ValueError("third-review IDs are absent from source packet")
    output = {key: source[key] for key in ("schema_version", "audit_id", "rubric_sha256",
                                           "coordinator_sha256")}
    output["items"] = items
    output["packet_sha256"] = document_identity(output, "packet_sha256")
    return write_private_json(Path(destination), output, repo=repo)


def combine_human_labels(first: AnnotationSet, second: AnnotationSet, third: AnnotationSet,
                         third_ids: tuple[str, ...]) -> CombinedLabels:
    sets = (first, second, third)
    if len({item.annotator_id for item in sets}) != 3:
        raise ValueError("three distinct human annotators are required")
    if len({(item.audit_id, item.rubric_sha256) for item in sets}) != 1:
        raise ValueError("annotation sets target different audits")
    identities = {item.coordinator_sha256 for item in sets if item.coordinator_sha256}
    if len(identities) > 1:
        raise ValueError("annotation sets target different coordinator identities")
    a, b, c = (_judgments_by_id(item) for item in sets)
    if set(a) != set(b) or set(c) != set(third_ids):
        raise ValueError("annotation coverage does not match review selection")
    combined = []
    for identifier in sorted(a):
        submissions = [a[identifier], b[identifier]]
        reason = None
        if identifier in c:
            submissions.append(c[identifier])
            if ((a[identifier]["verdict"], a[identifier]["category"]) !=
                    (b[identifier]["verdict"], b[identifier]["category"])):
                reason = "disagreement"
            elif "insufficient-context" in (a[identifier]["verdict"], b[identifier]["verdict"]):
                reason = "uncertainty"
            else:
                reason = "agreement-qa"
        votes = [(row["verdict"], row["category"]) for row in submissions
                 if row["verdict"] != "insufficient-context"]
        winner = next((vote for vote in set(votes) if votes.count(vote) >= 2), None)
        combined.append(CombinedLabel(identifier, winner[0] if winner else None,
                                      winner[1] if winner else None, winner is None, reason,
                                      tuple(submissions)))
    return CombinedLabels(first.audit_id, first.rubric_sha256, tuple(combined))


def item_sha256(item: AuditItem) -> str:
    value = json.dumps([item.language, item.id, item.code, item.doc],
                       ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(value.encode()).hexdigest()


def build_overlay(artifact: ArtifactInput, label_package: dict) -> dict:
    required = {"schema_version", "audit_id", "rubric_sha256", "coordinator_sha256",
                "label_package_sha256", "gate", "evidence", "labels"}
    if not isinstance(label_package, dict) or set(label_package) != required:
        raise ValueError("human label package fields are invalid")
    if (label_package.get("schema_version") != 1 or
            label_package["label_package_sha256"] !=
            document_identity(label_package, "label_package_sha256")):
        raise ValueError("human label package identity mismatch")
    if not isinstance(label_package.get("audit_id"), str) or not label_package["audit_id"]:
        raise ValueError("human label package audit_id is invalid")
    for field in ("rubric_sha256", "coordinator_sha256", "label_package_sha256"):
        if not isinstance(label_package.get(field), str) or not re.fullmatch(
                r"[0-9a-f]{64}", label_package[field]):
            raise ValueError(f"human label package {field} is invalid")
    gate, evidence = label_package.get("gate"), label_package.get("evidence")
    if (not isinstance(gate, dict) or set(gate) != {"status", "qualification", "reasons"} or
            gate.get("status") not in ("pass", "unverified", "escalate") or
            not isinstance(gate.get("reasons"), list) or
            not isinstance(evidence, dict) or
            evidence.get("coordinator_sha256") != label_package["coordinator_sha256"]):
        raise ValueError("human label package audit evidence is invalid")
    rows = []
    by_key = {item.key: item for item in artifact.items}
    labels = label_package.get("labels")
    if not isinstance(labels, list):
        raise ValueError("human label package labels are invalid")
    seen, seen_blind = set(), set()
    for human in sorted(labels, key=lambda row: (row.get("language", ""), row.get("id", ""))):
        if (not isinstance(human, dict) or set(human) != {"blind_id", "id", "language",
                "item_sha256", "human_verdict", "human_category"}):
            raise ValueError("human label package row is invalid")
        key = (canonical_language(human.get("language", "")), human.get("id"))
        if (not isinstance(human.get("blind_id"), str) or not human["blind_id"] or
                not isinstance(human.get("id"), str) or not human["id"] or
                not isinstance(human.get("item_sha256"), str) or
                not re.fullmatch(r"[0-9a-f]{64}", human["item_sha256"])):
            raise ValueError("human label package row identity is invalid")
        if key in seen:
            raise ValueError("human label package contains duplicate rows")
        seen.add(key)
        if human["blind_id"] in seen_blind:
            raise ValueError("human label package contains duplicate blind IDs")
        seen_blind.add(human["blind_id"])
        verdict, category = human.get("human_verdict"), human.get("human_category")
        if verdict not in ("consistent", "inconsistent"):
            raise ValueError("overlay human verdict is invalid")
        if verdict == "inconsistent" and category not in ("direct-mismatch", "over-promise"):
            raise ValueError("inconsistent overlay requires a category")
        if verdict == "consistent" and category is not None:
            raise ValueError("consistent overlay category must be null")
        if key not in by_key:
            continue
        item = by_key[key]
        if human.get("item_sha256") != item_sha256(item):
            raise ValueError("human label package item content hash mismatch")
        rows.append({"id": item.id, "language": item.language, "item_sha256": item_sha256(item),
                     "human_verdict": verdict, "human_category": category})
    overlay = {"schema_version": 1, "audit_id": label_package["audit_id"],
               "source_artifact_sha256": artifact.sha256,
               "label_package_sha256": label_package["label_package_sha256"],
               "coordinator_sha256": label_package["coordinator_sha256"],
               "rubric_sha256": label_package["rubric_sha256"],
               "generation_mode": "census" if len(rows) == artifact.row_count else "sample",
               "coverage": len(rows) / artifact.row_count, "rows": rows}
    overlay["overlay_sha256"] = document_identity(overlay, "overlay_sha256")
    return overlay


def rescore_overlay(artifact: ArtifactInput, overlay: dict, *, label_package: dict,
                    coordinator: dict, rubric: Path) -> dict:
    try:
        from .metrics import score
    except ImportError:  # Direct script execution through label_audit.py.
        from metrics import score
    expected = {"schema_version", "audit_id", "source_artifact_sha256",
                "label_package_sha256", "coordinator_sha256", "rubric_sha256",
                "generation_mode", "coverage", "rows", "overlay_sha256"}
    if not isinstance(overlay, dict) or set(overlay) != expected:
        raise ValueError("overlay fields are invalid")
    if (not isinstance(coordinator, dict) or
            coordinator.get("coordinator_sha256") !=
            document_identity(coordinator, "coordinator_sha256")):
        raise ValueError("rescore coordinator identity mismatch")
    expected_overlay = build_overlay(artifact, label_package)
    rubric_sha256 = sha256_file(Path(rubric))
    if (label_package.get("coordinator_sha256") != coordinator["coordinator_sha256"] or
            label_package.get("rubric_sha256") != rubric_sha256 or
            coordinator.get("rubric_sha256") != rubric_sha256 or
            coordinator.get("audit_id") != label_package.get("audit_id") or
            overlay != expected_overlay):
        raise ValueError("overlay derivation does not match the referenced human evidence")
    coordinator_items = coordinator.get("items")
    if not isinstance(coordinator_items, list):
        raise ValueError("overlay derivation coordinator items are invalid")
    by_blind = {row.get("blind_id"): row for row in coordinator_items if isinstance(row, dict)}
    package_labels = label_package["labels"]
    if (len(by_blind) != len(coordinator_items) or
            set(by_blind) != {row["blind_id"] for row in package_labels}):
        raise ValueError("overlay derivation does not exactly cover coordinator items")
    for row in package_labels:
        source = by_blind[row["blind_id"]]
        if (source.get("id") != row["id"] or source.get("language") != row["language"] or
                not isinstance(source.get("code"), str) or not isinstance(source.get("doc"), str)):
            raise ValueError("overlay derivation label does not match coordinator item")
        bound = json.dumps([row["language"], row["id"], source["code"], source["doc"]],
                           ensure_ascii=False, separators=(",", ":"))
        if hashlib.sha256(bound.encode()).hexdigest() != row["item_sha256"]:
            raise ValueError("overlay derivation item content hash mismatch")
    if overlay.get("schema_version") != 1:
        raise ValueError("overlay schema is invalid")
    if not isinstance(overlay.get("audit_id"), str) or not overlay["audit_id"]:
        raise ValueError("overlay audit_id is invalid")
    for field in ("source_artifact_sha256", "label_package_sha256", "coordinator_sha256",
                  "rubric_sha256", "overlay_sha256"):
        if not isinstance(overlay.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", overlay[field]):
            raise ValueError(f"overlay {field} is invalid")
    if overlay.get("overlay_sha256") != document_identity(overlay, "overlay_sha256"):
        raise ValueError("overlay identity mismatch")
    if overlay.get("source_artifact_sha256") != artifact.sha256:
        raise ValueError("overlay source artifact hash mismatch")
    rows = overlay.get("rows", [])
    if (len(rows) != artifact.row_count or overlay.get("coverage") != 1.0 or
            overlay.get("generation_mode") != "census"):
        raise ValueError("exact rescoring requires 100% human-label coverage")
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"id", "language", "item_sha256",
                "human_verdict", "human_category"}):
            raise ValueError("overlay row fields are invalid")
        verdict, category = row.get("human_verdict"), row.get("human_category")
        if (verdict not in ("consistent", "inconsistent") or
                (verdict == "consistent" and category is not None) or
                (verdict == "inconsistent" and category not in ("direct-mismatch", "over-promise"))):
            raise ValueError("overlay human label is invalid")
    human = {(row["language"], row["id"]): row for row in rows}
    if len(human) != len(rows):
        raise ValueError("overlay contains duplicate rows")
    original_rows, corrected_rows = [], []
    for item in artifact.items:
        row = human.get(item.key)
        if row is None or row.get("item_sha256") != item_sha256(item):
            raise ValueError("overlay item content hash mismatch")
        common = {"language": item.language, "final_status": item.final_status,
                  "final_verdict": item.final_verdict}
        original_rows.append({**common, "label": item.label, "category": item.category})
        corrected_rows.append({**common, "label": row["human_verdict"],
                               "category": row["human_category"]})
    return {"source_artifact_sha256": artifact.sha256, "original": score(original_rows),
            "corrected": score(corrected_rows),
            "audit_id": overlay["audit_id"], "overlay_sha256": overlay["overlay_sha256"],
            "coordinator_sha256": overlay["coordinator_sha256"],
            "label_package_sha256": overlay["label_package_sha256"],
            "rubric_sha256": overlay["rubric_sha256"]}


def repository_key(identifier: str) -> str:
    parts = identifier.split("/")
    if len(parts) < 3 or not parts[0] or not parts[1]:
        raise ValueError(f"benchmark id lacks owner/project repository: {identifier}")
    return "/".join(parts[:2])


def human_export_row(label: CombinedLabel, coordinator_item: dict) -> dict:
    if label.unresolved or label.final_verdict not in ("consistent", "inconsistent"):
        raise ValueError("only resolved human labels can be exported")
    if coordinator_item.get("blind_id") != label.blind_id:
        raise ValueError("human label does not match coordinator item")
    row = {"blind_id": label.blind_id, "id": coordinator_item["id"],
           "language": canonical_language(coordinator_item["language"]),
           "human_verdict": label.final_verdict, "human_category": label.final_category}
    if all(isinstance(coordinator_item.get(field), str) for field in ("code", "doc")):
        value = json.dumps([row["language"], row["id"], coordinator_item["code"],
                            coordinator_item["doc"]], ensure_ascii=False, separators=(",", ":"))
        row["item_sha256"] = hashlib.sha256(value.encode()).hexdigest()
    return row


def split_by_repository(labels: CombinedLabels, coordinator_items: list[dict], *, split_key: bytes,
                        development_fraction: float = 0.60) -> SplitResult:
    if len(split_key) < 32:
        raise ValueError("split key must be at least 32 bytes")
    if not 0 < development_fraction < 1:
        raise ValueError("development fraction must be between zero and one")
    mapping = {row["blind_id"]: row for row in coordinator_items}
    if set(mapping) != {label.blind_id for label in labels.labels}:
        raise ValueError("split coordinator coverage does not match final labels")
    if any(label.unresolved for label in labels.labels):
        raise ValueError("unresolved labels cannot be split")
    repositories = {}
    for label in labels.labels:
        repository = repository_key(mapping[label.blind_id]["id"])
        repositories.setdefault(repository, []).append(label.blind_id)
    ordered = sorted(repositories, key=lambda repo:
                     hmac.new(split_key, repo.encode(), hashlib.sha256).digest())
    rank = {repository: index for index, repository in enumerate(ordered)}
    label_by_id = {label.blind_id: label for label in labels.labels}
    repo_cell_counts = {}
    for repository, identifiers in repositories.items():
        counts = {}
        for identifier in identifiers:
            cell = (mapping[identifier]["language"], label_by_id[identifier].final_verdict,
                    label_by_id[identifier].final_category)
            counts[cell] = counts.get(cell, 0) + 1
        repo_cell_counts[repository] = counts
    repo_cells = {repository: set(counts) for repository, counts in repo_cell_counts.items()}
    all_cells = set().union(*repo_cells.values())
    candidates = {cell: sorted((repository for repository, cells in repo_cells.items()
                                if cell in cells), key=rank.get)
                  for cell in all_cells}
    sparse = [cell for cell, values in candidates.items() if len(values) < 2]
    if sparse:
        raise ValueError(f"repository split cannot cover human-label cells twice: {sorted(sparse)}")
    assignment: dict[str, bool] = {}

    def supports(cell, development: bool) -> bool:
        return any(assignment.get(repository) is development for repository in candidates[cell])

    def assign_supports() -> bool:
        unsatisfied = [cell for cell in all_cells
                       if not supports(cell, True) or not supports(cell, False)]
        if not unsatisfied:
            return True
        cell = min(unsatisfied, key=lambda value: (len(candidates[value]), value))
        need_dev, need_hold = not supports(cell, True), not supports(cell, False)
        if need_dev and need_hold:
            for development in candidates[cell]:
                if development in assignment:
                    continue
                assignment[development] = True
                for holdout in candidates[cell]:
                    if holdout == development or holdout in assignment:
                        continue
                    assignment[holdout] = False
                    if assign_supports():
                        return True
                    del assignment[holdout]
                del assignment[development]
            return False
        desired = True if need_dev else False
        for repository in candidates[cell]:
            if repository in assignment:
                continue
            assignment[repository] = desired
            if assign_supports():
                return True
            del assignment[repository]
        return False

    if not assign_supports():
        raise ValueError("repository split cannot satisfy human-label cell coverage")
    target_rows = len(labels.labels) * development_fraction
    development_rows = sum(len(repositories[repository]) for repository, value in assignment.items()
                           if value)
    for repository in sorted((repo for repo in ordered if repo not in assignment),
                             key=lambda repo: (-len(repositories[repo]), rank[repo])):
        size = len(repositories[repository])
        take = abs(development_rows + size - target_rows) <= abs(development_rows - target_rows)
        assignment[repository] = take
        development_rows += size if take else 0

    cell_totals = {cell: sum(counts.get(cell, 0) for counts in repo_cell_counts.values())
                   for cell in all_cells}

    def balance_score(values: dict[str, bool]) -> tuple[float, float, float, float]:
        row_fraction = (sum(len(repositories[repository]) for repository, value in values.items()
                            if value) / len(labels.labels))
        cell_fractions = []
        for cell in sorted(all_cells):
            development = sum(repo_cell_counts[repository].get(cell, 0)
                              for repository, value in values.items() if value)
            cell_fractions.append(development / cell_totals[cell])
        row_delta = abs(row_fraction - development_fraction)
        cell_deltas = [abs(value - development_fraction) for value in cell_fractions]
        excesses = [max(0.0, row_delta - SPLIT_ROW_TOLERANCE)] + [
            max(0.0, value - SPLIT_CELL_TOLERANCE) for value in cell_deltas]
        return max(excesses), sum(excesses), row_delta, sum(cell_deltas)

    # Whole repositories are indivisible. Improve the seeded allocation with deterministic flips
    # and swaps, then fail closed if no locally reachable allocation clears declared tolerances.
    while True:
        current = balance_score(assignment)
        best_score, best_change = current, None
        for repository in ordered:
            candidate = dict(assignment); candidate[repository] = not candidate[repository]
            score = balance_score(candidate)
            if score < best_score:
                best_score, best_change = score, (repository,)
        development = [repo for repo in ordered if assignment[repo]]
        holdout = [repo for repo in ordered if not assignment[repo]]
        for left in development:
            for right in holdout:
                candidate = dict(assignment); candidate[left] = False; candidate[right] = True
                score = balance_score(candidate)
                if score < best_score:
                    best_score, best_change = score, (left, right)
        if best_change is None:
            break
        for repository in best_change:
            assignment[repository] = not assignment[repository]

    row_fraction = (sum(len(repositories[repository]) for repository, value in assignment.items()
                        if value) / len(labels.labels))
    if abs(row_fraction - development_fraction) > SPLIT_ROW_TOLERANCE + 1e-12:
        raise ValueError("repository split row balance exceeds declared 5% tolerance")
    for cell in sorted(all_cells):
        development_count = sum(repo_cell_counts[repository].get(cell, 0)
                                for repository, value in assignment.items() if value)
        fraction = development_count / cell_totals[cell]
        if abs(fraction - development_fraction) > SPLIT_CELL_TOLERANCE + 1e-12:
            raise ValueError(f"repository split cell balance exceeds declared 15% tolerance: {cell}")

    for repository in ordered:
        if repository not in assignment:
            raise AssertionError("repository split assignment is incomplete")
    development_repositories = {repository for repository, development in assignment.items()
                                if development}
    development_ids = sorted(identifier for repo in development_repositories
                             for identifier in repositories[repo])
    holdout_repositories = set(ordered) - development_repositories
    holdout_ids = sorted(identifier for repo in holdout_repositories
                         for identifier in repositories[repo])
    for name, identifiers in (("development", development_ids), ("holdout", holdout_ids)):
        cells = {(mapping[identifier]["language"], label_by_id[identifier].final_verdict,
                  label_by_id[identifier].final_category) for identifier in identifiers}
        missing = all_cells - cells
        if missing:
            raise ValueError(f"{name} split has sparse human-label cell coverage: {sorted(missing)}")
    return SplitResult(tuple(development_ids), tuple(holdout_ids),
                       tuple(sorted(development_repositories)), tuple(sorted(holdout_repositories)))
