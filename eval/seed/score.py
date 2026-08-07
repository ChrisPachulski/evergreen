#!/usr/bin/env python3
"""Score a read-only `/evergreen:seed` run against its fixed fixture inventory.

Usage:
    python3 score.py <run-output.txt> [--gate] [--json] [--manifest PATH]
                     [--write-baseline]

This scorer is pure stdlib and makes zero model calls. It tolerantly reads JSON
objects from the run's trailing JSONL block: non-JSON lines are ignored so agent
prose and malformed noise do not make the scorer operationally fail. It grades
only the five S rows in the manifest; U rows are inventory-only and still count
toward coverage.

The classification gate catches the planted verdicts. It separately reports
over-population (a documented/not-worthy case called worthy, or visibly drafted)
and under-population (a worthy case declined). It cannot prove that a proposed
document is really about a symbol when the body never names that symbol.

The claim-ledger gate catches a missing `path:line` citation. It cannot prove
the citation is true, belongs to the claim, or points at the current fixture.
The speculation gate splits ordinary Markdown prose into sentence-like units and
uses normalized substring matching against claim rows; a unit on a line bearing
`seed:gap` is exempt. It can catch an unledgered copied sentence, but cannot
catch a paraphrase sharing no tokens with its ledger row, intent hidden in code
blocks, or a gap marker placed misleadingly beside unrelated prose. The additive
gate catches proposal paths that already exist in `eval/fixture/`, not deleted
lines in an actual diff because the judged run is read-only. Coverage catches
bad arithmetic and an incomplete inventory count; per-doc bound catches declared
documents over 60 lines. Unknown candidate symbols are printed for hand audit
and never gate.

Without `--gate`, any failed gate exits 2. `--gate` additionally compares the
current 0/1 gate scores with `baseline.json` and fails only if a recorded number
goes down. `--write-baseline` deliberately writes that baseline with a run
identity; it is never written automatically. Exit codes: 0 pass, 2 gate fail,
1 operational error.
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
FIXTURE = HERE.parent / "fixture"
ROOT = HERE.parent.parent
BASELINE = HERE / "baseline.json"
SCHEMA_VERSION = 1
CODE_REF_RE = re.compile(r"\S+:\d+\b")
SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+(?=\s|$)|$)")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise ValueError(message)


def normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def load_rows(text: str) -> list[dict[str, Any]]:
    """Return JSON objects from noisy output, matching eval/score.py tolerance."""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def load_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    required = {"id", "symbol", "module", "expected", "note"}
    if not rows or set(rows[0]) != required:
        fail(f"invalid manifest columns in {path}")
    return rows


def declarative_units(body: str) -> list[str]:
    """Sentence-like prose units, excluding headings, comments, and fenced code."""
    units: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "<!--", "```", "~~~")):
            continue
        stripped = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", stripped)
        for match in SENTENCE_RE.finditer(stripped):
            unit = match.group(0).strip()
            if unit and re.search(r"[A-Za-z]", unit):
                units.append(unit)
    return units


def claim_matches(unit: str, claims: list[dict[str, Any]]) -> bool:
    needle = normalize(unit)
    for claim in claims:
        haystack = normalize(claim.get("claim", ""))
        if needle and haystack and (needle in haystack or haystack in needle):
            return True
    return False


def existing_fixture_path(value: object) -> bool:
    path = Path(str(value))
    candidates = [path] if path.is_absolute() else [FIXTURE / path, ROOT / path]
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            if resolved.is_file() and resolved.is_relative_to(FIXTURE.resolve()):
                return True
        except OSError:
            continue
    return False


def gate_result(ok: bool, details: Any) -> dict[str, Any]:
    return {"pass": ok, "details": details}


def score(text: str, manifest_path: Path) -> dict[str, Any]:
    rows = load_rows(text)
    manifest = load_manifest(manifest_path)
    candidates = [row for row in rows if row.get("type") == "candidate"]
    claims = [row for row in rows if row.get("type") == "claim"]
    docs = [row for row in rows if row.get("type") == "doc"]
    coverage_rows = [row for row in rows if row.get("type") == "coverage"]
    by_symbol = {str(row.get("symbol")): row for row in candidates}
    known = {row["symbol"] for row in manifest}
    graded = [row for row in manifest if row["expected"] != "unscored"]

    classification: list[dict[str, str]] = []
    over_population: list[str] = []
    under_population: list[str] = []
    other_misses: list[str] = []
    doc_text = "\n".join(str(row.get("body", "")) for row in docs)
    for row in graded:
        actual = str(by_symbol.get(row["symbol"], {}).get("verdict", "missing"))
        hit = actual == row["expected"]
        classification.append({"id": row["id"], "symbol": row["symbol"],
                               "expected": row["expected"], "actual": actual,
                               "result": "hit" if hit else "miss"})
        drafted = bool(re.search(rf"\b{re.escape(row['symbol'])}\b", doc_text))
        if not hit and row["expected"] in {"documented", "not-worthy"} and (
                actual == "worthy" or drafted):
            over_population.append(row["id"])
        elif not hit and row["expected"] == "worthy":
            under_population.append(row["id"])
        elif not hit:
            other_misses.append(row["id"])
    gates: dict[str, dict[str, Any]] = {}
    gates["classification"] = gate_result(
        all(row["result"] == "hit" for row in classification),
        {"rows": classification, "over_population": over_population,
         "under_population": under_population, "other_misses": other_misses},
    )

    invalid_claims = [row for row in claims
                      if not CODE_REF_RE.search(str(row.get("code_ref", "")))]
    gates["goal 3 — claim ledger"] = gate_result(
        not invalid_claims, {"claims": len(claims), "invalid": invalid_claims})

    speculation: list[dict[str, str]] = []
    for doc in docs:
        body = str(doc.get("body", ""))
        for line in body.splitlines():
            marked = "seed:gap" in line
            for unit in declarative_units(line):
                if not marked and not claim_matches(unit, claims):
                    speculation.append({"path": str(doc.get("path", "")), "sentence": unit})
    gates["goal 5 — no unmarked speculation"] = gate_result(
        not speculation, {"unmarked": speculation})

    existing = [str(row.get("path", "")) for row in docs
                if existing_fixture_path(row.get("path", ""))]
    gates["goal 6 — purely additive"] = gate_result(
        not existing, {"existing_fixture_paths": existing})

    coverage_detail: dict[str, Any] = {"rows": coverage_rows, "manifest_total": len(manifest)}
    coverage_ok = len(coverage_rows) == 1
    if coverage_ok:
        coverage = coverage_rows[0]
        try:
            parts = [int(coverage[key]) for key in
                     ("documented", "worthy", "not_worthy", "deferred")]
            coverage_ok = sum(parts) == int(coverage["N"]) == len(manifest)
        except (KeyError, TypeError, ValueError):
            coverage_ok = False
    gates["coverage"] = gate_result(coverage_ok, coverage_detail)

    invalid_bounds: list[dict[str, Any]] = []
    for doc in docs:
        try:
            valid = int(doc["lines"]) <= 60
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            invalid_bounds.append(doc)
    gates["per-doc bound"] = gate_result(not invalid_bounds,
                                          {"invalid": invalid_bounds})

    unknown = [row for row in candidates if str(row.get("symbol")) not in known]
    scores = {name: int(gate["pass"]) for name, gate in gates.items()}
    return {"schema_version": SCHEMA_VERSION, "gates": gates, "scores": scores,
            "unknown_candidates": unknown, "passed": all(scores.values())}


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
    for name, gate in result["gates"].items():
        print(f"{name}: {'PASS' if gate['pass'] else 'FAIL'}")
        details = gate["details"]
        if name == "classification":
            for row in details["rows"]:
                print(f"  {row['result']}: {row['id']} {row['expected']} -> {row['actual']}")
            print("  over-population: " + (", ".join(details["over_population"]) or "none"))
            print("  under-population: " + (", ".join(details["under_population"]) or "none"))
        elif not gate["pass"]:
            print(f"  {json.dumps(details, sort_keys=True)}")
    if result["unknown_candidates"]:
        print("unknown candidates (hand audit, not gated):")
        for row in result["unknown_candidates"]:
            print(f"  {row.get('symbol')} rank {row.get('rank')}")
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
