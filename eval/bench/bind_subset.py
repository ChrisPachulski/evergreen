#!/usr/bin/env python3
"""Bind a screened subset to its parent rows, split assignment, and exact bytes.

The parent datasets and manifest freeze candidate contents and IDs into dev/holdout before
selection. The library accepts only value-equivalent JSON rows matching either a declared
exclusion set or the complete two-of-three screen result. The CLI requires the bound screen vote
ledger. It emits a schema-v1 manifest for the retained bytes, so a frozen judge lane need not
evaluate candidates the screen rejected.
"""

import argparse
import hashlib
import json
from pathlib import Path
import sys

try:
    from . import validate_labels
    from .artifact import read_bytes
    from .make_split import repository
    from .split_manifest import (
        MAX_DATASET_BYTES, MAX_MANIFEST_BYTES, MAX_ROWS, load_split_bindings_bytes,
        _loads_strict,
    )
except ImportError:  # Direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import validate_labels
    from artifact import read_bytes
    from make_split import repository
    from split_manifest import (
        MAX_DATASET_BYTES, MAX_MANIFEST_BYTES, MAX_ROWS, load_split_bindings_bytes,
        _loads_strict,
    )


def _rows(payload, label):
    try:
        rows = [_loads_strict(line) for line in payload.splitlines() if line.strip()]
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not valid JSONL") from error
    if not rows or len(rows) > MAX_ROWS:
        raise ValueError(f"{label} row count is invalid")
    return rows


def _retained_ids(parent_rows, parent_payload, ledger_payload, assignments, split):
    try:
        ledger = _loads_strict(ledger_payload)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("screen vote ledger is not valid JSON") from error
    binding = ledger.get("binding") if isinstance(ledger, dict) else None
    votes = ledger.get("votes") if isinstance(ledger, dict) else None
    expected_binding_fields = {
        "annotators", "cli_executable_sha256", "cli_version", "dataset_sha256",
        "screen_protocol_sha256",
    }
    protocol_digest = hashlib.sha256(Path(validate_labels.__file__).read_bytes()).hexdigest()
    parent_digest = hashlib.sha256(parent_payload).hexdigest()
    validate_labels._validate_annotators()
    if (not isinstance(ledger, dict) or set(ledger) != {
            "schema_version", "binding", "votes"} or ledger["schema_version"] != 1 or
            not isinstance(binding, dict) or set(binding) != expected_binding_fields or
            binding["annotators"] != validate_labels.ANNOTATORS or
            not isinstance(binding["cli_executable_sha256"], str) or
            len(binding["cli_executable_sha256"]) != 64 or
            any(character not in "0123456789abcdef"
                for character in binding["cli_executable_sha256"]) or
            not isinstance(binding["cli_version"], str) or not binding["cli_version"] or
            binding["dataset_sha256"] != parent_digest or
            binding["screen_protocol_sha256"] != protocol_digest or
            not isinstance(votes, dict)):
        raise ValueError("screen vote ledger binding or schema is invalid")
    parent_ids = [row["id"] for row in parent_rows]
    if set(votes) != set(parent_ids):
        raise ValueError("screen vote ledger must exactly cover the parent dataset")
    retained = []
    for row in parent_rows:
        per_model = votes.get(row["id"])
        if (not isinstance(per_model, dict) or
                set(per_model) != set(validate_labels.ANNOTATORS) or
                any(value not in ("consistent", "inconsistent")
                    for value in per_model.values())):
            raise ValueError("screen vote ledger contains incomplete or invalid votes")
        confirmations = sum(
            per_model[model] == row["label"] for model in validate_labels.ANNOTATORS
        )
        if assignments[row["id"]] == split and confirmations >= 2:
            retained.append(row["id"])
    return retained


def build_manifest_bytes(
    payload, parent_payloads, parent_manifest_payload, split, *,
    vote_ledger_payload=None, excluded_ids=None,
):
    if split not in ("dev", "holdout"):
        raise ValueError("split must be dev or holdout")
    rows = _rows(payload, "screened split dataset")
    ids = [row.get("id") if isinstance(row, dict) else None for row in rows]
    if (any(not isinstance(pair_id, str) or not pair_id for pair_id in ids) or
            len(ids) != len(set(ids))):
        raise ValueError("screened split dataset IDs must be unique non-empty strings")
    languages = {row.get("language", "python") for row in rows}
    if len(languages) != 1 or any(not isinstance(value, str) or not value for value in languages):
        raise ValueError("screened split dataset must contain exactly one language")
    assignments, row_datasets, declarations = load_split_bindings_bytes(
        parent_manifest_payload
    )
    parent_by_id = {}
    parent_rows_in_order = []
    expected_declarations = set()
    for parent_dataset_payload in parent_payloads:
        parent_rows = _rows(parent_dataset_payload, "parent split dataset")
        parent_languages = {row.get("language", "python") for row in parent_rows}
        if len(parent_languages) != 1:
            raise ValueError("parent split dataset must contain exactly one language")
        parent_digest = hashlib.sha256(parent_dataset_payload).hexdigest()
        expected_declarations.add((parent_digest, next(iter(parent_languages))))
        for row in parent_rows:
            pair_id = row.get("id") if isinstance(row, dict) else None
            if (not isinstance(pair_id, str) or not pair_id or
                    pair_id in parent_by_id):
                raise ValueError(
                    "parent split dataset IDs must be unique non-empty strings"
                )
            parent_by_id[pair_id] = row
            parent_rows_in_order.append(row)
            if row_datasets.get(pair_id) != parent_digest:
                raise ValueError("parent manifest does not exactly bind the parent dataset")
    if (not parent_by_id or declarations != expected_declarations or
            set(assignments) != set(parent_by_id)):
        raise ValueError("parent manifest does not exactly bind the parent dataset")
    if any(assignments.get(pair_id) != split for pair_id in ids):
        raise ValueError(f"every screened row must belong to the declared {split} split")
    if (vote_ledger_payload is None) == (excluded_ids is None):
        raise ValueError("declare exactly one deterministic subset policy")
    if vote_ledger_payload is not None:
        if len(parent_payloads) != 1:
            raise ValueError("a screen vote ledger must bind exactly one parent dataset")
        retained_ids = _retained_ids(
            parent_rows_in_order, parent_payloads[0], vote_ledger_payload,
            assignments, split,
        )
    else:
        excluded_ids = set(excluded_ids)
        if (any(not isinstance(pair_id, str) or not pair_id for pair_id in excluded_ids) or
                not excluded_ids <= set(parent_by_id)):
            raise ValueError("excluded IDs must exist in the parent dataset")
        retained_ids = [
            row["id"] for row in parent_rows_in_order
            if assignments[row["id"]] == split and row["id"] not in excluded_ids
        ]
    if ids != retained_ids:
        raise ValueError("screened dataset does not equal the deterministic retained set")
    if any(parent_by_id.get(pair_id) != row for pair_id, row in zip(ids, rows)):
        raise ValueError("screened rows must exactly match the parent dataset")

    digest = hashlib.sha256(payload).hexdigest()
    return {
        "schema_version": 1,
        "datasets": [{"sha256": digest, "language": next(iter(languages))}],
        "rows": [{
            "id": pair_id,
            "dataset_sha256": digest,
            "project": repository(pair_id),
            "split": split,
        } for pair_id in ids],
    }


def build_manifest(
    dataset, parent_datasets, parent_manifest, split, *, vote_ledger=None,
    excluded_ids=None,
):
    payload = read_bytes(dataset, MAX_DATASET_BYTES, label="screened split dataset")
    parent_payloads = [
        read_bytes(path, MAX_DATASET_BYTES, label="parent split dataset")
        for path in parent_datasets
    ]
    parent_manifest_payload = read_bytes(
        parent_manifest, MAX_MANIFEST_BYTES, label="parent split manifest"
    )
    vote_ledger_payload = (
        read_bytes(vote_ledger, MAX_MANIFEST_BYTES, label="screen vote ledger")
        if vote_ledger is not None else None
    )
    return build_manifest_bytes(
        payload, parent_payloads, parent_manifest_payload, split,
        vote_ledger_payload=vote_ledger_payload, excluded_ids=excluded_ids,
    )


def manifest_bytes(document):
    return (json.dumps(document, indent=1, sort_keys=True) + "\n").encode()


def build_screen_receipt_bytes(
    output_payload, parent_payload, parent_manifest_payload, ledger_payload,
    split, manifest_document,
):
    """Bind the deterministic screen ancestry needed by a frozen paid lane."""
    expected = build_manifest_bytes(
        output_payload, [parent_payload], parent_manifest_payload, split,
        vote_ledger_payload=ledger_payload,
    )
    if manifest_document != expected:
        raise ValueError("screen manifest does not match the deterministic retained set")
    try:
        ledger = _loads_strict(ledger_payload)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("screen vote ledger is not valid JSON") from error
    return {
        "schema_version": 1,
        "selection_protocol": "three-model-majority-v1",
        "split": split,
        "rows": len(manifest_document["rows"]),
        "output_dataset_sha256": hashlib.sha256(output_payload).hexdigest(),
        "output_manifest_sha256": hashlib.sha256(
            manifest_bytes(manifest_document)
        ).hexdigest(),
        "parent_dataset_sha256": hashlib.sha256(parent_payload).hexdigest(),
        "parent_manifest_sha256": hashlib.sha256(parent_manifest_payload).hexdigest(),
        "vote_ledger_sha256": hashlib.sha256(ledger_payload).hexdigest(),
        "screen_binding": ledger["binding"],
    }


def build_screen_receipt(
    dataset, parent_dataset, parent_manifest, vote_ledger, split, manifest_document,
):
    output_payload = read_bytes(dataset, MAX_DATASET_BYTES, label="screened split dataset")
    parent_payload = read_bytes(
        parent_dataset, MAX_DATASET_BYTES, label="parent split dataset"
    )
    parent_manifest_payload = read_bytes(
        parent_manifest, MAX_MANIFEST_BYTES, label="parent split manifest"
    )
    ledger_payload = read_bytes(
        vote_ledger, MAX_MANIFEST_BYTES, label="screen vote ledger"
    )
    return build_screen_receipt_bytes(
        output_payload, parent_payload, parent_manifest_payload, ledger_payload,
        split, manifest_document,
    )


def receipt_bytes(document):
    return (json.dumps(document, indent=1, sort_keys=True) + "\n").encode()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--parent-dataset", required=True, action="append", type=Path)
    parser.add_argument("--parent-manifest", required=True, type=Path)
    parser.add_argument("--vote-ledger", required=True, type=Path)
    parser.add_argument("--split", required=True, choices=("dev", "holdout"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--receipt-out", required=True, type=Path)
    args = parser.parse_args(argv)
    document = build_manifest(
        args.dataset, args.parent_dataset, args.parent_manifest, args.split,
        vote_ledger=args.vote_ledger,
    )
    receipt = build_screen_receipt(
        args.dataset, args.parent_dataset[0], args.parent_manifest, args.vote_ledger,
        args.split, document,
    )
    args.out.write_bytes(manifest_bytes(document))
    args.receipt_out.write_bytes(receipt_bytes(receipt))
    print(f"bound {len(document['rows'])} {args.split} rows -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
