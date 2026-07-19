#!/usr/bin/env python3
"""Freeze a deterministic development probe from an already-bound dev JSONL dataset.

Selects exactly `--positive-count` label=inconsistent (drift-positive) rows and
`--control-count` label=consistent (control) rows. Within each label stratum, rows are
ranked by HMAC-SHA256(key=parent_dataset_sha256 lowercase-hex encoded UTF-8, msg=id encoded
UTF-8) ascending, with ties broken by id, and the lowest-N are kept. The output preserves
each selected row's original JSON bytes and parent row order; it is never reserialized. The
selector reads only the parent dataset (which already carries `label`) and never opens a
separate outcome or vote artifact, so labels affect stratum assignment only.
"""

import argparse
import hashlib
import hmac
import json
from pathlib import Path
import sys
import time

try:
    from . import artifact
    from .split_manifest import _loads_strict
except ImportError:  # Direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import artifact
    from split_manifest import _loads_strict


MAX_PARENT_BYTES = 8 * 1024 * 1024
POSITIVE_LABEL = "inconsistent"
CONTROL_LABEL = "consistent"
SELECTION_PROTOCOL = (
    "probe-hmac-sha256-stratified-lowest-n-v1: within each label stratum, rank rows by "
    "HMAC-SHA256(key=parent_dataset_sha256 lowercase-hex encoded UTF-8, msg=id encoded "
    "UTF-8) ascending, tie-break by id ascending, and keep the lowest N; output rows are "
    "the original parent JSON bytes in parent row order, never reserialized"
)


def _parse_rows(payload, label):
    try:
        return [_loads_strict(line) for line in payload.splitlines() if line.strip()]
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not valid JSONL") from error


def build_probe_bytes(payload, positive_count, control_count, *, expect_parent_sha256=None):
    """Pure selection over the raw parent bytes; no filesystem access."""
    if (not isinstance(positive_count, int) or isinstance(positive_count, bool) or
            positive_count <= 0):
        raise ValueError("--positive-count must be a positive integer")
    if (not isinstance(control_count, int) or isinstance(control_count, bool) or
            control_count <= 0):
        raise ValueError("--control-count must be a positive integer")

    parent_sha256 = hashlib.sha256(payload).hexdigest()
    if expect_parent_sha256 is not None and expect_parent_sha256 != parent_sha256:
        raise ValueError(
            f"parent dataset sha256 mismatch: expected {expect_parent_sha256}, "
            f"found {parent_sha256} (parent dataset has changed)"
        )

    lines = [line for line in payload.splitlines() if line.strip()]
    rows = _parse_rows(payload, "parent development dataset")

    entries = []  # (id, label, line) in original parent order
    ids_seen = set()
    language = None
    for line, item in zip(lines, rows):
        if not isinstance(item, dict):
            raise ValueError("parent development dataset row must be a JSON object")
        pair_id = item.get("id")
        if not isinstance(pair_id, str) or not pair_id:
            raise ValueError("parent development dataset row id must be a non-empty string")
        if pair_id in ids_seen:
            raise ValueError(f"duplicate id in parent development dataset: {pair_id!r}")
        ids_seen.add(pair_id)
        label = item.get("label")
        if label not in (POSITIVE_LABEL, CONTROL_LABEL):
            raise ValueError(f"unexpected label for id {pair_id!r}: {label!r}")
        row_language = item.get("language")
        if not isinstance(row_language, str) or not row_language:
            raise ValueError(f"row language must be a non-empty string: {pair_id!r}")
        if language is None:
            language = row_language
        elif row_language != language:
            raise ValueError(f"mixed languages in parent dataset: {language!r} and {row_language!r}")
        entries.append((pair_id, label, line))

    key = parent_sha256.encode("ascii")
    selected_ids = set()
    for label, count in ((POSITIVE_LABEL, positive_count), (CONTROL_LABEL, control_count)):
        stratum = [pair_id for pair_id, row_label, _line in entries if row_label == label]
        if len(stratum) < count:
            raise ValueError(
                f"parent development dataset has only {len(stratum)} {label!r} rows; "
                f"need {count}"
            )
        ranked = sorted(
            stratum,
            key=lambda pair_id: (
                hmac.new(key, pair_id.encode("utf-8"), hashlib.sha256).digest(), pair_id,
            ),
        )
        selected_ids.update(ranked[:count])

    selected = [(pair_id, label, line) for pair_id, label, line in entries
                if pair_id in selected_ids]
    output_payload = b"".join(line + b"\n" for _id, _label, line in selected)
    output_sha256 = hashlib.sha256(output_payload).hexdigest()

    receipt = {
        "schema_version": 1,
        "selection_protocol": SELECTION_PROTOCOL,
        "language": language,
        "positive_label": POSITIVE_LABEL,
        "control_label": CONTROL_LABEL,
        "positive_count": positive_count,
        "control_count": control_count,
        "parent_dataset_sha256": parent_sha256,
        "output_dataset_sha256": output_sha256,
        "rows": [{"id": pair_id, "label": label} for pair_id, label, _line in selected],
    }
    return output_payload, receipt


def build_probe(parent_path, positive_count, control_count, *, expect_parent_sha256=None):
    """Select a probe from a parent development dataset on disk."""
    parent_path = Path(parent_path)
    payload = artifact.read_bytes(
        parent_path, MAX_PARENT_BYTES, timeout=30, label="parent development dataset"
    )
    output_payload, receipt = build_probe_bytes(
        payload, positive_count, control_count, expect_parent_sha256=expect_parent_sha256,
    )
    receipt = dict(receipt)
    receipt["parent_filename"] = parent_path.name
    return output_payload, receipt


def verify_probe_receipt(receipt, parent_path, *, max_bytes=MAX_PARENT_BYTES, timeout=30):
    """Reject a receipt whose recorded parent hash no longer matches the parent on disk."""
    deadline = time.monotonic() + timeout
    actual = artifact.sha256_file(Path(parent_path), max_bytes=max_bytes, deadline=deadline)
    expected = receipt.get("parent_dataset_sha256")
    if not isinstance(expected, str) or actual != expected:
        raise ValueError(
            f"probe receipt parent sha256 mismatch: receipt says {expected!r}, parent is "
            f"now {actual!r} (parent dataset has changed)"
        )


def receipt_bytes(document):
    return (json.dumps(document, indent=1, sort_keys=True) + "\n").encode()


def _validate_hex_digest(value, label):
    if (not isinstance(value, str) or len(value) != 64 or
            any(character not in "0123456789abcdef" for character in value)):
        raise ValueError(f"{label} must be a lowercase hex SHA-256 digest")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("parent", type=Path, help="already-bound development JSONL dataset")
    parser.add_argument("--positive-count", type=int, required=True,
                         help="number of label=inconsistent rows to select")
    parser.add_argument("--control-count", type=int, required=True,
                         help="number of label=consistent rows to select")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--receipt-out", type=Path, required=True)
    parser.add_argument(
        "--expect-parent-sha256", default=None,
        help="fail closed if the parent dataset's sha256 does not match this hex digest",
    )
    args = parser.parse_args(argv)
    if args.expect_parent_sha256 is not None:
        _validate_hex_digest(args.expect_parent_sha256, "--expect-parent-sha256")

    output_payload, receipt = build_probe(
        args.parent, args.positive_count, args.control_count,
        expect_parent_sha256=args.expect_parent_sha256,
    )
    args.out.write_bytes(output_payload)
    args.receipt_out.write_bytes(receipt_bytes(receipt))
    print(
        f"probe: {receipt['positive_count']} {POSITIVE_LABEL} + "
        f"{receipt['control_count']} {CONTROL_LABEL} -> {args.out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
