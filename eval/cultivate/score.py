#!/usr/bin/env python3
"""Score a read-only `/evergreen:cultivate` run against a disposable Git fixture.

Usage:
    python3 score.py <run-output.txt> [--gate] [--json] [--manifest PATH]
                     [--write-baseline]

This scorer is pure stdlib and makes zero model calls. It tolerantly reads JSON
objects from the run's trailing JSONL block: prose and malformed lines are
ignored. The manifest is the fixture inventory; every planted path must receive
the expected verdict. A decoy dynamically loaded by `plugin_registry.py` is
reported first: proposing `orphan` or `delete-proposed` for it is the expensive
false positive this exam is designed to catch.

The gates cover the fixture-side branches of the six cultivate hard goals:
inventory counts and the untracked gap; evidence attached to every verdict; no
bare clean conclusion plus a stated non-coverage list; assertion-backed tested
claims; and a specific not-checked list. Goal 4 grades only its fallback here:
the fixture has no remote, so a passing run reports `gh_ran: false` and
`visibility: unchecked`. The live-remote branch of goal 4 is not covered by
this exam and would require a throwaway public repository.

The scorer can check that a reference-graph result was emitted, but cannot
re-run it, prove a cited line actually asserts, assess dynamic references other
than the planted decoy, or validate the live-remote `gh` path. `--gate`
compares 0/1 gate scores against `baseline.json`; `--write-baseline` writes it
only when explicitly requested. Exit codes: 0 pass, 2 gate fail, 1 operational
error.
"""
import argparse
import csv
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
BASELINE = HERE / "baseline.json"
SCHEMA_VERSION = 1
CLEAN_CONCLUSION_RE = re.compile(
    r"(?im)^\s*(?:final\s+)?(?:verdict|conclusion|result)\s*[:=-]\s*(?:clean|no slop)\b|"
    r"\b(?:repo(?:sitory)?|it|this)\s+(?:is|looks|appears)\s+(?:clean|free of slop)\b"
)
VAGUE_NOT_CHECKED = {"all", "everything", "nothing", "none", "checked everything"}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise ValueError(message)


def load_rows(text: str) -> list[dict[str, Any]]:
    """Return JSON objects from noisy model output."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    required = {"id", "path", "kind", "tracked", "expected", "note"}
    if not rows or set(rows[0]) != required:
        fail(f"invalid manifest columns in {path}")
    if any(row["tracked"] not in {"yes", "no"} for row in rows):
        fail(f"invalid tracked value in {path}")
    return rows


def gate_result(ok: bool, details: Any) -> dict[str, Any]:
    return {"pass": ok, "details": details}


def as_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def specific_not_checked(rows: list[dict[str, Any]]) -> list[str]:
    items: list[str] = []
    for row in rows:
        value = row.get("items")
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item.strip():
                items.append(item.strip())
    return [item for item in items if item.casefold() not in VAGUE_NOT_CHECKED]


def score(text: str, manifest_path: Path) -> dict[str, Any]:
    rows = load_rows(text)
    manifest = load_manifest(manifest_path)
    verdicts = [row for row in rows if row.get("type") == "verdict"]
    inventories = [row for row in rows if row.get("type") == "inventory"]
    exposures = [row for row in rows if row.get("type") == "exposure"]
    tested = [row for row in rows if row.get("type") == "tested"]
    not_checked = [row for row in rows if row.get("type") == "not_checked"]

    expected_tracked = sum(row["tracked"] == "yes" for row in manifest)
    expected_on_disk = len(manifest)
    expected_gap = expected_on_disk - expected_tracked
    inventory_details: dict[str, Any] = {
        "rows": inventories,
        "expected": {"tracked": expected_tracked, "on_disk": expected_on_disk,
                     "gap_accounted": expected_gap},
    }
    inventory_ok = len(inventories) == 1
    if inventory_ok:
        inventory = inventories[0]
        inventory_ok = (
            as_int(inventory.get("tracked")) == expected_tracked
            and as_int(inventory.get("on_disk")) == expected_on_disk
            and as_int(inventory.get("gap_accounted")) == expected_gap
        )

    invalid_evidence = [row for row in verdicts
                        if not isinstance(row.get("evidence"), str)
                        or not re.search(r"\bgit\s+grep\b", row["evidence"], re.I)]
    reference_ok = bool(verdicts) and not invalid_evidence

    noncoverage = specific_not_checked(not_checked)
    clean_matches = CLEAN_CONCLUSION_RE.findall(text)
    no_clean_ok = not clean_matches and bool(noncoverage)

    fallback_exposure = [row for row in exposures if row.get("gh_ran") is False
                         and row.get("visibility") == "unchecked"]
    exposure_ok = len(exposures) == 1 and len(fallback_exposure) == 1

    assertion_free_refs = [row for row in tested if isinstance(row.get("test_ref"), str)
                           and row["test_ref"].startswith("tests/test_runner.py:")]
    invalid_tested = [row for row in tested if row.get("status") == "tested" and
                      (not isinstance(row.get("test_ref"), str) or ":" not in row["test_ref"])]
    tested_ok = bool(tested) and not invalid_tested and not assertion_free_refs

    by_path = {str(row.get("path")): row for row in verdicts}
    classification: list[dict[str, str]] = []
    for row in manifest:
        actual = str(by_path.get(row["path"], {}).get("verdict", "missing"))
        classification.append({"id": row["id"], "path": row["path"],
                               "expected": row["expected"], "actual": actual,
                               "result": "hit" if actual == row["expected"] else "miss"})
    decoys = [row for row in classification if row["id"] and
              next(item for item in manifest if item["id"] == row["id"])["kind"] == "decoy"]
    decoy_false_positives = [row for row in decoys if row["actual"] in {"orphan", "delete-proposed"}]
    classification_ok = all(row["result"] == "hit" for row in classification)

    gates: dict[str, dict[str, Any]] = {
        "goal 1 — inventory": gate_result(inventory_ok, inventory_details),
        "goal 2 — reference graph": gate_result(
            reference_ok, {"verdicts": len(verdicts), "invalid_evidence": invalid_evidence}),
        "goal 3 — no bare clean": gate_result(
            no_clean_ok, {"clean_conclusions": clean_matches, "not_checked": noncoverage}),
        "goal 4 — exposure fallback": gate_result(
            exposure_ok, {"rows": exposures, "expected": {"gh_ran": False, "visibility": "unchecked"}}),
        "goal 5 — tested-or-gap": gate_result(
            tested_ok, {"tested_rows": tested, "invalid_tested": invalid_tested,
                        "assertion_free_refs": assertion_free_refs}),
        "goal 6 — states what was not checked": gate_result(
            bool(noncoverage), {"items": noncoverage}),
        "classification vs manifest": gate_result(
            classification_ok, {"rows": classification,
                                "decoy_false_positives": decoy_false_positives}),
    }
    scores = {name: int(gate["pass"]) for name, gate in gates.items()}
    return {"schema_version": SCHEMA_VERSION, "gates": gates, "scores": scores,
            "decoy_false_positives": len(decoy_false_positives),
            "passed": all(scores.values())}


def load_baseline(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read baseline {path}: {exc}")
    if data.get("schema_version") != SCHEMA_VERSION or not isinstance(data.get("scores"), dict):
        fail(f"invalid baseline {path}")
    return data


def baseline_document(result: dict[str, Any], run_path: Path, text: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "scores": result["scores"],
        "run_identity": {
            "path": str(run_path),
            "sha256": hashlib.sha256(text.encode()).hexdigest(),
            "recorded_at": datetime.now(UTC).isoformat(),
        },
    }


def render(result: dict[str, Any], gate_status: str | None) -> None:
    print(f"DECOY FALSE POSITIVES: {result['decoy_false_positives']}")
    for name, gate in result["gates"].items():
        print(f"{name}: {'PASS' if gate['pass'] else 'FAIL'}")
        details = gate["details"]
        if name == "classification vs manifest":
            for row in details["rows"]:
                print(f"  {row['result']}: {row['id']} {row['expected']} -> {row['actual']}")
            print("  decoy false positives: " +
                  (", ".join(row["id"] for row in details["decoy_false_positives"]) or "none"))
        elif not gate["pass"]:
            print(f"  {json.dumps(details, sort_keys=True)}")
    if gate_status:
        print(f"baseline: {gate_status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_output", type=Path)
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--manifest", type=Path, default=HERE / "manifest.tsv")
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args()
    try:
        text = args.run_output.read_text()
        result = score(text, args.manifest)
        gate_status: str | None = None
        regressed: list[str] = []
        if args.gate:
            if BASELINE.exists():
                baseline = load_baseline(BASELINE)
                regressed = [name for name, old in baseline["scores"].items()
                             if int(result["scores"].get(name, 0)) < int(old)]
                gate_status = "regressed: " + ", ".join(regressed) if regressed else "no regression"
            else:
                gate_status = "no baseline"
        if args.write_baseline:
            BASELINE.write_text(json.dumps(baseline_document(result, args.run_output, text),
                                           indent=2, sort_keys=True) + "\n")
            gate_status = "written"
        result["baseline"] = {"status": gate_status, "regressed": regressed}
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        render(result, gate_status)
    return 2 if (regressed if args.gate else not result["passed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
