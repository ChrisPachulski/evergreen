#!/usr/bin/env python3
"""Read-only offline replay, comparison, and split selection for benchmark artifacts."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

try:
    from .artifact import MAX_ARTIFACT_BYTES, read_bytes
    from .metrics import rows_from_transcript, score
    from .resolver import resolve, route_screen_v3
    from .runner import artifact_rows
    from .split_manifest import load_split_manifest
    from .trial import _execution_ledger
except ImportError:  # Direct script execution.
    from artifact import MAX_ARTIFACT_BYTES, read_bytes
    from metrics import rows_from_transcript, score
    from resolver import resolve, route_screen_v3
    from runner import artifact_rows
    from split_manifest import load_split_manifest
    from trial import _execution_ledger


MAX_LABEL_BYTES = 16 * 1024 * 1024
MAX_DATASET_BYTES = 64 * 1024 * 1024
SAFE_LANGUAGE = re.compile(r"^[A-Za-z0-9._-]+$")
LABEL_FIELDS = {"id", "label", "category"}
LABELS = {"consistent", "inconsistent"}
CATEGORIES = {None, "direct-mismatch", "over-promise"}
DECISION_FIELDS = (
    "final_status", "final_verdict", "verdict", "category", "why", "contested",
)
OPTIONAL_DECISION_FIELDS = ("semantic_status", "proof", "claim", "evidence", "execution")


def _decision_differences(stored, replayed):
    fields = [*DECISION_FIELDS, *(
        field for field in OPTIONAL_DECISION_FIELDS if field in stored or field in replayed
    )]
    return [field for field in fields if stored.get(field) != replayed.get(field)]


def _v3_execution_ledger(stages):
    """Recompute the got.execution provider-attempt ledger purely from persisted v3 stages, the
    same way trial._judge_cascade_v3 built it when the row was first judged. Never trusts the
    stored "route" reason text — the route is recomputed straight from the screen stage."""
    screen_result = stages.get("screen")
    route = route_screen_v3(screen_result)
    return _execution_ledger(route["decision"], screen_result, stages.get("jury"))


def replay_rows(rows, resolver_id, expect_stored=False):
    """Return a deep replay of rows, optionally requiring stored-decision parity.

    Resolver v3 rows also carry a got.execution provider-attempt ledger alongside the decision
    fields resolve() itself owns; it is recomputed independently from the persisted stages and
    folded into the replayed decision so expect_stored compares it exactly like every other
    decision field — a decision that reproduces without its ledger matching would otherwise
    hide a broken accounting change.
    """
    replayed = copy.deepcopy(rows)
    for original, row in zip(rows, replayed):
        got = original.get("got") or {}
        stages = got.get("stages") or {}
        decision = resolve(stages, resolver_id)
        if resolver_id == "v3" and "execution" in got:
            decision["execution"] = _v3_execution_ledger(stages)
        differences = _decision_differences(got, decision) if expect_stored else []
        if differences:
            field = differences[0]
            raise ValueError(
                f"{original.get('id')}: {field} stored={got.get(field)} "
                f"replayed={decision.get(field)}"
            )
        row["got"] = decision
    return replayed


def bounded_snapshot(path, max_bytes, label):
    """Read and hash one bounded immutable byte snapshot."""
    payload = read_bytes(path, max_bytes, label=label)
    return payload, hashlib.sha256(payload).hexdigest()


def artifact_snapshot(path):
    """Parse and hash one bounded immutable byte snapshot of an artifact."""
    payload, digest = bounded_snapshot(
        path, MAX_ARTIFACT_BYTES, label="benchmark replay artifact"
    )
    return json.loads(payload), digest


def _jsonl(path, max_bytes=MAX_DATASET_BYTES, label="dataset"):
    payload = read_bytes(path, max_bytes, label=label)
    try:
        return [json.loads(line) for line in payload.splitlines() if line.strip()]
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} is not valid JSONL") from error


def _source_rows(datasets):
    rows = {}
    for path in datasets:
        for row in _jsonl(path):
            pair_id = row.get("id")
            if not isinstance(pair_id, str) or not pair_id or pair_id in rows:
                raise ValueError("source datasets contain invalid or duplicate ids")
            rows[pair_id] = row
    return rows


def _context_rows(path, language, source):
    result = {}
    for row in _jsonl(path, label="context dataset"):
        pair_id = row.get("id")
        original = source.get(pair_id)
        if original is None or row.get("language", "python") != language:
            raise ValueError("context dataset contains an unknown id or language")
        without_context = {key: value for key, value in row.items() if key != "context"}
        if without_context != original or "context" not in row:
            raise ValueError("context dataset changed non-context fields")
        if pair_id in result:
            raise ValueError("context dataset contains duplicate ids")
        result[pair_id] = row
    expected = {pair_id for pair_id, row in source.items()
                if row.get("language", "python") == language}
    if set(result) != expected:
        raise ValueError("context dataset does not exactly cover its language")
    return result


def _labels(path, selected):
    values = {}
    for row in _jsonl(path, max_bytes=MAX_LABEL_BYTES, label="private labels"):
        if not isinstance(row, dict) or set(row) != LABEL_FIELDS:
            raise ValueError("private label row has unknown or missing fields")
        if row["id"] in values or row["label"] not in LABELS or row["category"] not in CATEGORIES:
            raise ValueError("private label row is invalid or duplicated")
        values[row["id"]] = row
    if set(values) != selected:
        raise ValueError("private labels do not exactly cover selected split")
    return values


def _atomic_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def select_split(manifest, split, labels, datasets, output_dir, context_datasets=None):
    """Select public split IDs before opening and joining exact private labels."""
    if split not in {"dev", "holdout"}:
        raise ValueError("split must be dev or holdout")
    mapping = load_split_manifest(Path(manifest), [Path(path) for path in datasets])
    source = _source_rows(datasets)
    selected = {pair_id for pair_id, assigned in mapping.items() if assigned == split}
    replacements = {}
    for language, path in (context_datasets or {}).items():
        if not SAFE_LANGUAGE.fullmatch(language):
            raise ValueError("context language is unsafe")
        replacements.update(_context_rows(path, language, source))
    label_rows = _labels(labels, selected)  # Open labels only after manifest and selection validate.

    by_language = {}
    for pair_id in sorted(selected):
        row = copy.deepcopy(replacements.get(pair_id, source[pair_id]))
        row.update({key: label_rows[pair_id][key] for key in ("label", "category")})
        language = row.get("language", "python")
        if not isinstance(language, str) or not SAFE_LANGUAGE.fullmatch(language):
            raise ValueError("dataset language is unsafe")
        by_language.setdefault(language, []).append(row)
    output = {}
    for language, rows in sorted(by_language.items()):
        path = Path(output_dir) / f"{language}.jsonl"
        _atomic_jsonl(path, rows)
        output[language] = path
    return output


def _snap_rows(rows):
    snapped = copy.deepcopy(rows)
    for row in snapped:
        got = row.get("got") or {}
        snap = (got.get("stages", {}).get("snap") or {}).get("value") or {}
        if snap.get("verdict") in {"consistent", "inconsistent"}:
            got.update({"final_status": "complete", "final_verdict": snap["verdict"]})
        row["got"] = got
    return snapped


def _replay_main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+")
    parser.add_argument("--resolver", choices=("v1", "v2", "v3"), default="v1")
    parser.add_argument("--expect-stored", action="store_true")
    parser.add_argument("--compare-snap", action="store_true")
    args = parser.parse_args(argv)
    rows = []
    hashes = []
    for path_text in args.artifacts:
        path = Path(path_text)
        document, digest = artifact_snapshot(path)
        rows.extend(artifact_rows(document))
        hashes.append(digest)
    replayed = replay_rows(rows, args.resolver, expect_stored=args.expect_stored)
    completed = sum(row["got"]["final_status"] == "complete" for row in replayed)
    abstained = len(replayed) - completed
    print(f"{args.resolver} parity: {completed} completed rows reproduced; 0 differences; "
          f"{abstained} stored abstention{'s' if abstained != 1 else ''} preserved")
    print("artifact sha256: " + ",".join(hashes))
    if args.compare_snap:
        for language in sorted({row.get("language", "unknown") for row in rows}):
            language_rows = [row for row in rows if row.get("language", "unknown") == language]
            current = score(rows_from_transcript(language_rows))
            snap = score(rows_from_transcript(_snap_rows(language_rows)))
            print(f"{language}\tv1={current['precision']:.3f}/{current['recall']:.3f}/"
                  f"{current['f1']:.3f}\tsnap={snap['precision']:.3f}/"
                  f"{snap['recall']:.3f}/{snap['f1']:.3f}")
    return 0


def _select_main(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--split", choices=("dev", "holdout"), required=True)
    parser.add_argument("--labels", required=True)
    parser.add_argument("--dataset", action="append", required=True)
    parser.add_argument("--context-dataset", action="append", default=[])
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args(argv)
    contexts = {}
    for value in args.context_dataset:
        language, separator, path = value.partition("=")
        if not separator or language in contexts:
            raise ValueError("context dataset must be unique LANGUAGE=PATH")
        contexts[language] = Path(path)
    paths = select_split(
        Path(args.manifest), args.split, Path(args.labels), [Path(p) for p in args.dataset],
        Path(args.output_dir), context_datasets=contexts,
    )
    for language, path in sorted(paths.items()):
        _payload, digest = bounded_snapshot(
            path, MAX_DATASET_BYTES, label="generated split output"
        )
        print(f"{language}\t{digest}\t{path}")
    return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "select-split":
        return _select_main(args[1:])
    return _replay_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
