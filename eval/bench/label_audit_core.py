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


def _source_item(row: dict, language: str, digest: str) -> AuditItem:
    for field in ("id", "code", "doc", "func", "label"):
        if not isinstance(row.get(field), str) or not row[field]:
            raise ValueError(f"source pool row {field} is invalid")
    return AuditItem(row["id"], language, row["code"], row["doc"], row["func"],
                     row["label"], row.get("category"), "unscored", None, digest)


def build_sample(artifacts: tuple[ArtifactInput, ...], source_pools: dict[str, SourcePool], *,
                 audit_id: str, seed: int = 20260713, tn_per_language: int = 25,
                 discarded_per_language: int = 20) -> SampleSelection:
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

    missing = []
    for language in ("python", "typescript", "rust", "go"):
        pool = source_pools.get(language)
        if pool is None:
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
    hashes = tuple(sorted((artifact.language, artifact.sha256) for artifact in artifacts) +
                   sorted((f"{language}-source", pool.sha256)
                          for language, pool in source_pools.items()))
    return SampleSelection(audit_id, seed, tuple(selected), tuple(sorted(missing)), hashes)


def blind_id(key: bytes, audit_id: str, item: AuditItem) -> str:
    if len(key) < 32:
        raise ValueError("blind key must be at least 32 bytes")
    message = "\0".join((audit_id, item.language, item.id)).encode()
    return "item-" + hmac.new(key, message, hashlib.sha256).hexdigest()[:24]


def _numbered(value: str) -> str:
    return "\n".join(f"{number} | {line}" for number, line in enumerate(value.splitlines(), 1))


def _atomic_json(path: Path, value: object, mode: int = 0o600) -> None:
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


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
    _atomic_json(a, {**base, "items": ordered("annotator-a")})
    _atomic_json(b, {**base, "items": ordered("annotator-b")})
    _atomic_json(source, {**base, "items": ordered("adjudicator")})
    _atomic_json(coordinator, {**base, "missing_discarded_languages":
                 list(selection.missing_discarded_languages), "input_hashes": dict(selection.input_hashes),
                 "items": [{"blind_id": opaque, "id": selected.item.id,
                            "language": selected.item.language, "label": selected.item.label,
                            "category": selected.item.category, "final_status": selected.item.final_status,
                            "final_verdict": selected.item.final_verdict, "stratum": selected.stratum,
                            "inclusion_probability": selected.inclusion_probability,
                            "source_kind": selected.source_kind,
                            "artifact_sha256": selected.item.artifact_sha256,
                            "code": selected.item.code, "doc": selected.item.doc,
                            "func": selected.item.func}
                           for opaque, selected in mapped]})
    descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(blind_key)
    return PacketOutputs(a, b, source, coordinator, key_path)


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
        if (a[identifier]["verdict"] != b[identifier]["verdict"] or
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
    packet_document = json.loads(read_bytes(Path(packet), MAX_AUDIT_INPUT_BYTES,
                                            label="annotation packet"))
    if set(document) != {"schema_version", "audit_id", "rubric_sha256", "annotator", "judgments"}:
        raise ValueError("annotation fields are invalid")
    if document.get("schema_version") != 1:
        raise ValueError("annotation schema_version must be 1")
    if (document.get("audit_id") != packet_document.get("audit_id") or
            document.get("rubric_sha256") != packet_document.get("rubric_sha256")):
        raise ValueError("annotations do not match packet audit and rubric")
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
                         tuple(judgments))


def write_third_packet(source_packet: Path, blind_ids: tuple[str, ...], destination: Path) -> Path:
    source = json.loads(read_bytes(Path(source_packet), MAX_AUDIT_INPUT_BYTES,
                                   label="adjudicator source packet"))
    wanted = set(blind_ids)
    items = [row for row in source.get("items", []) if row.get("blind_id") in wanted]
    if {row["blind_id"] for row in items} != wanted:
        raise ValueError("third-review IDs are absent from source packet")
    output = {key: source[key] for key in ("schema_version", "audit_id", "rubric_sha256")}
    output["items"] = items
    _atomic_json(Path(destination), output)
    return Path(destination)


def combine_human_labels(first: AnnotationSet, second: AnnotationSet, third: AnnotationSet,
                         third_ids: tuple[str, ...]) -> CombinedLabels:
    sets = (first, second, third)
    if len({item.annotator_id for item in sets}) != 3:
        raise ValueError("three distinct human annotators are required")
    if len({(item.audit_id, item.rubric_sha256) for item in sets}) != 1:
        raise ValueError("annotation sets target different audits")
    a, b, c = (_judgments_by_id(item) for item in sets)
    if set(a) != set(b) or set(c) != set(third_ids):
        raise ValueError("annotation coverage does not match review selection")
    combined = []
    for identifier in sorted(a):
        submissions = [a[identifier], b[identifier]]
        reason = None
        if identifier in c:
            submissions.append(c[identifier])
            if (a[identifier]["verdict"] != b[identifier]["verdict"]):
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


def build_overlay(artifact: ArtifactInput, labels: dict[tuple[str, str], tuple[str, str | None]], *,
                  rubric_sha256: str, label_package_sha256: str) -> dict:
    rows = []
    by_key = {item.key: item for item in artifact.items}
    for key, (verdict, category) in sorted(labels.items()):
        if key not in by_key:
            raise ValueError(f"overlay label does not match artifact: {key}")
        if verdict not in ("consistent", "inconsistent"):
            raise ValueError("overlay human verdict is invalid")
        if verdict == "inconsistent" and category not in ("direct-mismatch", "over-promise"):
            raise ValueError("inconsistent overlay requires a category")
        if verdict == "consistent" and category is not None:
            raise ValueError("consistent overlay category must be null")
        item = by_key[key]
        rows.append({"id": item.id, "language": item.language, "item_sha256": item_sha256(item),
                     "human_verdict": verdict, "human_category": category})
    return {"schema_version": 1, "source_artifact_sha256": artifact.sha256,
            "label_package_sha256": label_package_sha256, "rubric_sha256": rubric_sha256,
            "coverage": len(rows) / artifact.row_count, "rows": rows}


def rescore_overlay(artifact: ArtifactInput, overlay: dict) -> dict:
    try:
        from .metrics import score
    except ImportError:  # Direct script execution through label_audit.py.
        from metrics import score
    if overlay.get("source_artifact_sha256") != artifact.sha256:
        raise ValueError("overlay source artifact hash mismatch")
    rows = overlay.get("rows", [])
    if len(rows) != artifact.row_count or overlay.get("coverage") != 1.0:
        raise ValueError("exact rescoring requires 100% human-label coverage")
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
            "label_package_sha256": overlay["label_package_sha256"],
            "rubric_sha256": overlay["rubric_sha256"]}


def repository_key(identifier: str) -> str:
    parts = identifier.split("/")
    if len(parts) < 3 or not parts[0] or not parts[1]:
        raise ValueError(f"benchmark id lacks owner/project repository: {identifier}")
    return "/".join(parts[:2])


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
    target = round(len(ordered) * development_fraction)
    development_repositories = set(ordered[:target])
    development_ids = sorted(identifier for repo in development_repositories
                             for identifier in repositories[repo])
    holdout_repositories = set(ordered) - development_repositories
    holdout_ids = sorted(identifier for repo in holdout_repositories
                         for identifier in repositories[repo])
    label_by_id = {label.blind_id: label for label in labels.labels}
    for language in LANGUAGES:
        classes = {label_by_id[identifier].final_verdict for identifier in holdout_ids
                   if mapping[identifier]["language"] == language}
        if classes != {"consistent", "inconsistent"}:
            raise ValueError(f"holdout split has sparse binary coverage for {language}")
    return SplitResult(tuple(development_ids), tuple(holdout_ids),
                       tuple(sorted(development_repositories)), tuple(sorted(holdout_repositories)))
