#!/usr/bin/env python3
"""Generate a deterministic repository-grouped dev/holdout split manifest.

The ordering key is HMAC-SHA256 keyed by the *checked-in source dataset's* SHA-256 hex
digest — a value committed and documented before any v2 run existed — so the grouping is
reproducible and was never tunable against v2 outcomes. Repositories never cross splits.
Fails closed if either split would receive zero inconsistent rows.
"""
import argparse
import hashlib
import hmac
import json
from pathlib import Path


def repository(pair_id):
    parts = pair_id.split("/")
    if len(parts) < 4:
        raise ValueError(f"unexpected pair id shape: {pair_id!r}")
    return "/".join(parts[:2])


def assign(rows, key, development_fraction):
    groups = {}
    for row in rows:
        groups.setdefault(repository(row["id"]), []).append(row)
    ordered = sorted(groups, key=lambda repo:
                     hmac.new(key, repo.encode(), hashlib.sha256).digest())
    total = len(rows)
    dev_count = 0
    assignment = {}
    for repo in ordered:
        size = len(groups[repo])
        dev_deficit = development_fraction * total - dev_count
        assignment[repo] = "dev" if dev_deficit >= size / 2 else "holdout"
        if assignment[repo] == "dev":
            dev_count += size
    return assignment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="context-augmented full dataset JSONL")
    parser.add_argument("--key-sha256", required=True,
                        help="hex SHA-256 of the checked-in source dataset (the HMAC key)")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--development-fraction", type=float, default=0.60)
    args = parser.parse_args(argv)
    if len(args.key_sha256) != 64 or any(c not in "0123456789abcdef" for c in args.key_sha256):
        raise ValueError("--key-sha256 must be a lowercase hex SHA-256 digest")

    lines = [line for line in args.dataset.read_bytes().splitlines() if line.strip()]
    rows = [json.loads(line) for line in lines]
    assignment = assign(rows, args.key_sha256.encode(), args.development_fraction)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    stem = args.dataset.stem
    payloads = {}
    for split in ("dev", "holdout"):
        payload = b"".join(line + b"\n" for line, row in zip(lines, rows)
                           if assignment[repository(row["id"])] == split)
        path = args.out_dir / f"{stem}-{split}.jsonl"
        path.write_bytes(payload)
        payloads[split] = (path, hashlib.sha256(payload).hexdigest())

    languages = {row.get("language", "python") for row in rows}
    if len(languages) != 1:
        raise ValueError("dataset must contain exactly one language")
    language = next(iter(languages))
    for split in ("dev", "holdout"):
        positives = sum(1 for row in rows if row["label"] == "inconsistent"
                        and assignment[repository(row["id"])] == split)
        if positives == 0:
            raise ValueError(f"{split} split received zero inconsistent rows")
        count = sum(1 for row in rows if assignment[repository(row["id"])] == split)
        print(f"{split}: {count} rows, {positives} inconsistent -> {payloads[split][0]}")

    manifest = {
        "schema_version": 1,
        "datasets": [{"sha256": payloads[split][1], "language": language}
                     for split in ("dev", "holdout")],
        "rows": [{
            "id": row["id"],
            "dataset_sha256": payloads[assignment[repository(row["id"])]][1],
            "project": repository(row["id"]),
            "split": assignment[repository(row["id"])],
        } for row in rows],
    }
    manifest_path = args.out_dir / f"{stem}-split-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
