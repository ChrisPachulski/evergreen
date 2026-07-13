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


def _manifest(path, expected_declarations=None):
    document = _load_json(Path(path), MAX_MANIFEST_BYTES, "split manifest")
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


def load_split_assignments(path: Path) -> dict[str, str]:
    """Load the public ID-to-split mapping without opening any label-bearing dataset."""
    return _manifest(path)[0]


def load_split_manifest(path: Path, datasets: list[Path]) -> dict[str, str]:
    """Return ID-to-split mapping after exact hash, coverage, and grouping validation."""
    actual_set, expected_ids = _datasets(datasets)
    result, row_datasets, _declared_set, _document = _manifest(path, actual_set)
    if (set(result) != set(expected_ids) or
            any(row_datasets[pair_id] != digest for pair_id, digest in expected_ids.items())):
        raise ValueError("split manifest does not exactly cover dataset rows")
    return result


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        raise SystemExit("usage: split_manifest.py MANIFEST DATASET...")
    mapping = load_split_manifest(Path(args[0]), [Path(item) for item in args[1:]])
    document = _load_json(Path(args[0]), MAX_MANIFEST_BYTES, "split manifest")
    projects = {row["project"] for row in document["rows"]}
    print(f"split manifest valid: {len(mapping)} rows; {len(projects)} projects "
          "do not cross dev/holdout")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
