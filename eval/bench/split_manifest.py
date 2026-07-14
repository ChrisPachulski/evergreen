#!/usr/bin/env python3
"""Validate ID-only, repository-grouped benchmark split manifests."""

import hashlib
import json
from pathlib import Path
import sys

try:
    from .artifact import read_bytes
except ImportError:  # Direct script execution.
    from artifact import read_bytes


MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_DATASET_BYTES = 64 * 1024 * 1024
MAX_ROWS = 100_000
ALLOWED_TOP = {"schema_version", "datasets", "rows"}
ALLOWED_DATASET = {"sha256", "language"}
ALLOWED_ROW = {"id", "dataset_sha256", "project", "split"}
ORACLE_TOP = {"schema_version", "similarity_policy_sha256", "datasets", "rows"}
ORACLE_DATASET = {"sha256", "split", "rows"}
ORACLE_ROW = {"id", "dataset_sha256", "split"}
ORACLE_POLICY_SHA256 = "afe5010343623c9f413c3304350b751219f25137113422c241460a70260dfeb5"
SPLITS = {"dev", "holdout"}


def _load_json(path, max_bytes, label):
    try:
        return json.loads(read_bytes(path, max_bytes, label=label))
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSON") from error


def _datasets(paths):
    declarations = set()
    ids = {}
    for path in map(Path, paths):
        payload = read_bytes(path, MAX_DATASET_BYTES, label="split dataset")
        digest = hashlib.sha256(payload).hexdigest()
        rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
        if len(rows) > MAX_ROWS:
            raise ValueError("split dataset has too many rows")
        languages = {row.get("language", "python") for row in rows}
        if len(languages) != 1:
            raise ValueError("split dataset must contain exactly one language")
        language = next(iter(languages))
        declarations.add((digest, language))
        for row in rows:
            pair_id = row.get("id")
            if not isinstance(pair_id, str) or not pair_id or pair_id in ids:
                raise ValueError("split datasets contain invalid or duplicate row ids")
            ids[pair_id] = digest
    return declarations, ids


def _manifest_v1(document, expected_declarations=None):
    if not isinstance(document, dict) or set(document) != ALLOWED_TOP:
        raise ValueError("split manifest has unknown or missing fields")
    if document["schema_version"] != 1:
        raise ValueError("unsupported split manifest schema")

    declared = document["datasets"]
    if (not isinstance(declared, list) or not declared or
            any(not isinstance(item, dict) or set(item) != ALLOWED_DATASET
                for item in declared)):
        raise ValueError("split manifest datasets are malformed")
    declared_set = set()
    for item in declared:
        if (not isinstance(item["sha256"], str) or len(item["sha256"]) != 64 or
                any(character not in "0123456789abcdef" for character in item["sha256"]) or
                not isinstance(item["language"], str) or not item["language"]):
            raise ValueError("split manifest dataset declaration is malformed")
        declared_set.add((item["sha256"], item["language"]))
    if len(declared_set) != len(declared):
        raise ValueError("split manifest dataset declarations are duplicated")
    if (expected_declarations is not None and
            (declared_set != expected_declarations or len(declared_set) != len(declared))):
        raise ValueError("split manifest dataset declarations do not match inputs")
    rows = document["rows"]
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("split manifest rows are malformed")
    result = {}
    row_datasets = {}
    project_splits = {}
    declared_hashes = {digest for digest, _language in declared_set}
    for row in rows:
        if not isinstance(row, dict) or set(row) != ALLOWED_ROW:
            raise ValueError("split manifest row has forbidden or missing fields")
        pair_id = row["id"]
        project = row["project"]
        split = row["split"]
        id_parts = pair_id.split("/") if isinstance(pair_id, str) else []
        expected_project = "/".join(id_parts[:2]) if len(id_parts) >= 2 else None
        if (not isinstance(pair_id, str) or not pair_id or
                not isinstance(project, str) or project != expected_project or
                split not in SPLITS or
                not isinstance(row["dataset_sha256"], str) or
                len(row["dataset_sha256"]) != 64 or
                row["dataset_sha256"] not in declared_hashes):
            raise ValueError("split manifest row has invalid id, dataset, project, or split")
        if pair_id in result:
            raise ValueError("split manifest contains duplicate row id")
        previous = project_splits.setdefault(project, split)
        if previous != split:
            raise ValueError("project appears in both dev and holdout")
        result[pair_id] = split
        row_datasets[pair_id] = row["dataset_sha256"]
    return result, row_datasets, declared_set, document


def _manifest_v2(document, expected_declarations=None):
    if set(document) != ORACLE_TOP:
        raise ValueError("oracle split manifest has unknown or missing fields")
    policy = document["similarity_policy_sha256"]
    if (not isinstance(policy, str) or len(policy) != 64 or
            any(character not in "0123456789abcdef" for character in policy) or
            policy != ORACLE_POLICY_SHA256):
        raise ValueError("oracle split manifest policy hash is malformed")
    declared = document["datasets"]
    if (not isinstance(declared, list) or len(declared) != 2 or
            any(not isinstance(item, dict) or set(item) != ORACLE_DATASET
                for item in declared)):
        raise ValueError("oracle split package declarations are malformed")
    declared_set = set()
    by_hash = {}
    for item in declared:
        digest = item["sha256"]
        split = item["split"]
        count = item["rows"]
        if (not isinstance(digest, str) or len(digest) != 64 or
                any(character not in "0123456789abcdef" for character in digest) or
                split not in SPLITS or type(count) is not int or count < 0):
            raise ValueError("oracle split package declaration is malformed")
        declaration = (digest, split, count)
        if digest in by_hash or split in by_hash.values() or declaration in declared_set:
            raise ValueError("oracle split package declarations are duplicated")
        declared_set.add(declaration)
        by_hash[digest] = split
    if set(by_hash.values()) != SPLITS:
        raise ValueError("oracle split package declarations must cover both splits")
    if expected_declarations is not None and declared_set != expected_declarations:
        raise ValueError("oracle split package declarations do not match inputs")
    rows = document["rows"]
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValueError("oracle split manifest rows are malformed")
    result = {}
    row_datasets = {}
    counts = {digest: 0 for digest in by_hash}
    for row in rows:
        if not isinstance(row, dict) or set(row) != ORACLE_ROW:
            raise ValueError("oracle split manifest row has forbidden or missing fields")
        row_id = row["id"]
        digest = row["dataset_sha256"]
        split = row["split"]
        if (not isinstance(row_id, str) or len(row_id) != 71 or
                not row_id.startswith("oracle-") or
                any(character not in "0123456789abcdef" for character in row_id[7:]) or
                digest not in by_hash or split != by_hash[digest]):
            raise ValueError("oracle split manifest row identity is invalid")
        if row_id in result:
            raise ValueError("oracle split manifest contains duplicate row id")
        result[row_id] = split
        row_datasets[row_id] = digest
        counts[digest] += 1
    declared_counts = {digest: count for digest, _split, count in declared_set}
    if counts != declared_counts:
        raise ValueError("oracle split package row counts do not match declarations")
    return result, row_datasets, declared_set, document


def _manifest(path, expected_declarations=None):
    document = _load_json(Path(path), MAX_MANIFEST_BYTES, "split manifest")
    if (isinstance(document, dict) and type(document.get("schema_version")) is int and
            document["schema_version"] == 2):
        return _manifest_v2(document, expected_declarations)
    return _manifest_v1(document, expected_declarations)


def _oracle_packages(paths):
    declarations = set()
    ids = {}
    for path in map(Path, paths):
        payload = read_bytes(path, MAX_DATASET_BYTES, label="oracle split package")
        digest = hashlib.sha256(payload).hexdigest()
        try:
            rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
        except json.JSONDecodeError as error:
            raise ValueError("oracle split package is not valid JSONL") from error
        if len(rows) > MAX_ROWS:
            raise ValueError("oracle split package has too many rows")
        splits = {row.get("split") for row in rows if isinstance(row, dict)}
        if len(splits) != 1 or next(iter(splits)) not in SPLITS:
            raise ValueError("oracle split package must contain exactly one split")
        split = next(iter(splits))
        declarations.add((digest, split, len(rows)))
        for row in rows:
            row_id = row.get("id") if isinstance(row, dict) else None
            if not isinstance(row_id, str) or not row_id or row_id in ids:
                raise ValueError("oracle split packages contain invalid or duplicate row ids")
            ids[row_id] = (digest, split)
    return declarations, ids


def load_split_assignments(path: Path) -> dict[str, str]:
    """Load the public ID-to-split mapping without opening any label-bearing dataset."""
    return _manifest(path)[0]


def load_split_manifest(path: Path, datasets: list[Path]) -> dict[str, str]:
    """Return ID-to-split mapping after exact hash, coverage, and grouping validation."""
    document = _load_json(Path(path), MAX_MANIFEST_BYTES, "split manifest")
    if (isinstance(document, dict) and type(document.get("schema_version")) is int and
            document["schema_version"] == 2):
        actual_set, expected_ids = _oracle_packages(datasets)
        result, row_datasets, _declared_set, _document = _manifest(path, actual_set)
        if (set(result) != set(expected_ids) or any(
                row_datasets[row_id] != digest or result[row_id] != split
                for row_id, (digest, split) in expected_ids.items())):
            raise ValueError("oracle split manifest does not exactly cover package rows")
        return result
    actual_set, expected_ids = _datasets(datasets)
    result, row_datasets, _declared_set, _document = _manifest(path, actual_set)
    if (set(result) != set(expected_ids) or any(
            row_datasets[pair_id] != digest for pair_id, digest in expected_ids.items())):
        raise ValueError("split manifest does not exactly cover dataset rows")
    return result


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        raise SystemExit("usage: split_manifest.py MANIFEST DATASET...")
    mapping = load_split_manifest(Path(args[0]), [Path(item) for item in args[1:]])
    document = _load_json(Path(args[0]), MAX_MANIFEST_BYTES, "split manifest")
    if document["schema_version"] == 1:
        projects = {row["project"] for row in document["rows"]}
        print(f"split manifest valid: {len(mapping)} rows; {len(projects)} projects "
              "do not cross dev/holdout")
    else:
        print(f"oracle split manifest valid: {len(mapping)} ID-only rows; 2 private packages bound")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
