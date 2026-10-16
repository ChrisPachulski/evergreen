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
bad arithmetic and an incomplete inventory count. Goal 1 also gates candidate
identity: every manifest symbol must appear exactly once among the candidate
rows, with its declaration's module basename matching the manifest. Unknown symbols,
duplicates, and wrong modules fail the gate. Per-doc bound counts a doc's ACTUAL body lines
(never the self-reported `lines` field) against 60, and a missing or
non-string body fails it outright. The docs-stage judge row's own `docs`
count is checked against the number of unique written doc paths.

Without `--gate`, any failed gate exits 2. `--gate` additionally compares the
current 0/1 gate scores with `baseline.json` and fails only if a recorded number
goes down. `--write-baseline` deliberately writes that baseline with a run
identity; it is never written automatically. Exit codes: 0 pass, 2 gate fail,
1 operational error.
"""
import argparse
import csv
import hashlib
import collections
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


PATH_LINE_RE = re.compile(r"^[\w./+-]+:\d+$")
INSPECTED_VERDICTS = {"documented", "worthy", "not-worthy"}
COLD_STREAK = 10  # hard-goals/seed.md goal 2: stop after 10 cold verdicts


def gate_result(ok: bool, details: Any) -> dict[str, Any]:
    return {"pass": ok, "details": details}


def as_int(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def candidate_field_faults(row: dict[str, Any], module: str | None) -> list[str]:
    """Goal 1: every candidate row carries symbol, kind, declaration, rank."""
    faults: list[str] = []
    if not str(row.get("symbol", "")).strip():
        faults.append("no symbol")
    if not str(row.get("kind", "")).strip():
        faults.append("no kind")
    ref = str(row.get("code_ref") or row.get("declaration") or "").strip()
    if not PATH_LINE_RE.match(ref):
        faults.append(f"declaration is not path:line ({ref!r})")
    if module is not None and Path(ref.rsplit(":", 1)[0]).name != module:
        faults.append(f"declaration module does not match {module!r} ({ref!r})")
    if as_int(row.get("rank")) is None:
        faults.append("no rank")
    return faults


def gate_inventory(text: str, inventories: list[dict[str, Any]],
                   candidates: list[dict[str, Any]],
                   manifest: list[dict[str, str]]) -> dict[str, Any]:
    """Goal 1 — a complete, surface-shaped inventory with every field present."""
    manifest_total = len(manifest)
    known_modules = {row["symbol"]: row["module"] for row in manifest}
    known_symbols = set(known_modules)
    details: dict[str, Any] = {"rows": inventories, "manifest_total": manifest_total}
    if len(inventories) != 1:
        details["why"] = f"expected exactly one inventory row, saw {len(inventories)}"
        return gate_result(False, details)
    inventory = inventories[0]
    source = as_int(inventory.get("source_files"))
    scanned = as_int(inventory.get("scanned_files"))
    declared = as_int(inventory.get("candidates"))
    warnings = [str(w) for w in inventory.get("warnings", [])
                if isinstance(inventory.get("warnings"), list)]
    truncated = [w for w in warnings if "truncated" in w.lower()]
    # An `outside inventory` warning names files out of surface: it must be
    # quoted in the report as a named exclusion, not swallowed by the row.
    unquoted = [w for w in warnings if "outside inventory" in w.lower()
                and text.count(w) < 2]
    field_faults = {str(row.get("symbol")): candidate_field_faults(
                        row, known_modules.get(str(row.get("symbol"))))
                    for row in candidates}
    field_faults = {k: v for k, v in field_faults.items() if v}
    # Identity, not just count: every manifest symbol exactly once, nothing else.
    candidate_symbols = [str(row.get("symbol")) for row in candidates]
    duplicate_symbols = sorted({s for s in candidate_symbols
                                if candidate_symbols.count(s) > 1})
    unknown_symbols = sorted({s for s in candidate_symbols if s not in known_symbols})
    missing_symbols = sorted(known_symbols - set(candidate_symbols))
    ok = (source is not None and source == scanned
          and declared == len(candidates) == manifest_total
          and not truncated and not unquoted and not field_faults
          and not duplicate_symbols and not unknown_symbols and not missing_symbols)
    details.update({"source_files": source, "scanned_files": scanned,
                    "declared_candidates": declared,
                    "candidate_rows": len(candidates),
                    "truncated_warnings": truncated,
                    "unquoted_outside_inventory": unquoted,
                    "row_faults": field_faults,
                    "duplicate_symbols": duplicate_symbols,
                    "unknown_symbols": unknown_symbols,
                    "missing_symbols": missing_symbols})
    return gate_result(ok, details)


def gate_walk(candidates: list[dict[str, Any]], manifest_total: int) -> dict[str, Any]:
    """Goal 2 — the ranking is walked in order, and stops only when it goes cold."""
    ranks = [as_int(row.get("rank")) for row in candidates]
    ordered = ranks == list(range(1, len(candidates) + 1))
    verdicts = [str(row.get("verdict", "")) for row in candidates]
    inspected = [v for v in verdicts if v in INSPECTED_VERDICTS]
    # The walk is a prefix: every inspected row comes before every deferred one.
    prefix_ok = all(v in INSPECTED_VERDICTS for v in verdicts[:len(inspected)])
    deferred_ok = all(v == "deferred" for v in verdicts[len(inspected):])
    last = inspected[-COLD_STREAK:]
    cold = len(last) == COLD_STREAK and all(v != "worthy" for v in last)
    stop_ok = len(inspected) == manifest_total or cold
    ok = bool(ordered and prefix_ok and deferred_ok and stop_ok
              and len(candidates) == manifest_total)
    return gate_result(ok, {
        "ranks": ranks, "ordered_without_gaps": ordered,
        "inspected": len(inspected), "walk_is_a_prefix": prefix_ok,
        "remainder_deferred": deferred_ok,
        "stopped_at_N_or_cold": stop_ok, "cold_streak": cold})


def gate_winnow(winnows: list[dict[str, Any]], claim_rows: int) -> dict[str, Any]:
    """Goal 4 — the seeded docs were winnowed: certified == ledger, no drift."""
    details: dict[str, Any] = {"rows": winnows, "claim_rows": claim_rows}
    if len(winnows) != 1:
        details["why"] = f"expected exactly one winnow row, saw {len(winnows)}"
        return gate_result(False, details)
    row = winnows[0]
    certified, drift, unverified = (as_int(row.get("certified")),
                                    as_int(row.get("drift")),
                                    as_int(row.get("unverified")))
    ok = certified == claim_rows and drift == 0 and unverified == 0
    details.update({"certified": certified, "drift": drift,
                    "unverified": unverified})
    return gate_result(ok, details)


def gate_judge(judge_rows: list[dict[str, Any]], written: int,
              enforce_docs_count: bool = False) -> dict[str, Any]:
    """Second provider — required whenever a doc was written; optional when none was."""
    details: dict[str, Any] = {"rows": judge_rows, "written": written}
    if written == 0 and not judge_rows:
        details["why"] = "no docs written — the judge row is not required"
        return gate_result(True, details)
    if len(judge_rows) != 1:
        details["why"] = f"expected exactly one judge row, saw {len(judge_rows)}"
        return gate_result(False, details)
    row = judge_rows[0]
    status = row.get("status")
    details.update({"status": status, "provider": row.get("provider"),
                    "docs": row.get("docs"), "certified": row.get("certified"),
                    "unverified": row.get("unverified")})
    if status != "ok":
        details.update({"reason": row.get("reason"),
                        "why": f"judge status is {status!r}, not \"ok\""})
        return gate_result(False, details)
    if enforce_docs_count:
        judged_docs = as_int(row.get("docs"))
        if judged_docs != written:
            details["why"] = f"judge counted docs={judged_docs}, not {written} written"
            return gate_result(False, details)
    drift = as_int(row.get("drift"))
    details["drift"] = drift
    ok = drift == 0
    if not ok:
        details["why"] = f"judge reports drift={drift}, not 0"
    return gate_result(ok, details)


def gate_owner(rows: list[dict[str, Any]], docs: list[dict[str, Any]],
               coverage_rows: list[dict[str, Any]], by_symbol: dict[str, Any],
               approve: int | None) -> dict[str, Any]:
    """Goal 7 — the worthy list precedes the drafts and the owner sets the count."""
    order = {id(row): i for i, row in enumerate(rows)}
    recommendations = [row for row in rows if row.get("type") == "recommendation"]
    owners = [row for row in rows if row.get("type") == "owner"]
    first_doc = next((order[id(row)] for row in rows if row.get("type") == "doc"), None)
    listed_first = bool(recommendations) and (
        first_doc is None or order[id(recommendations[0])] < first_doc)
    worthy_list = recommendations[0].get("worthy", []) if recommendations else []
    recommended = as_int(recommendations[0].get("recommend")) if recommendations else None
    chosen = approve if approve is not None else (
        as_int(owners[0].get("chosen")) if owners else None)

    coverage = coverage_rows[0] if len(coverage_rows) == 1 else {}
    written = as_int(coverage.get("written"))
    worthy_count = as_int(coverage.get("worthy"))
    if written is None:
        written = len(docs)
    counts_ok = (worthy_count is not None
                 and (written == 0 if chosen is None
                      else 0 <= written <= chosen <= worthy_count))
    # A pre-diff `documented` candidate is already covered; writing it anyway is
    # the over-population failure this command exists to avoid.
    documented_written = [str(doc.get("symbol")) for doc in docs
                          if str(by_symbol.get(str(doc.get("symbol")), {})
                                 .get("verdict")) == "documented"]
    ok = bool(listed_first and recommended is not None and counts_ok
              and not documented_written
              and len(worthy_list) == (worthy_count or 0))
    return gate_result(ok, {
        "list_before_drafts": listed_first, "worthy_listed": len(worthy_list),
        "worthy_counted": worthy_count, "recommended": recommended,
        "chosen": chosen, "chosen_from": ("--approve" if approve is not None
                                          else "owner row" if owners else None),
        "written": written, "documented_candidates_written": documented_written})


def gate_reader_use(docs: list[dict[str, Any]], claims: list[dict[str, Any]],
                    by_symbol: dict[str, Any]) -> dict[str, Any]:
    """Goal 8 — each written doc earns a reader-use row and names its symbol."""
    faults: list[dict[str, Any]] = []
    for doc in docs:
        path, symbol = str(doc.get("path", "")), str(doc.get("symbol", ""))
        declaration = str(by_symbol.get(symbol, {}).get("code_ref")
                          or by_symbol.get(symbol, {}).get("declaration") or "")
        rows = [c for c in claims if str(c.get("doc", "")) == path
                and str(c.get("kind", "")).replace("_", "-") == "reader-use"]
        outside = [c for c in rows
                   if str(c.get("code_ref", "")).strip() != declaration.strip()]
        # Post-diff grep proxy: the exam run is read-only, so the written file is
        # the proposed body. A hit on a `seed:gap` line does not count.
        body_lines = [line for line in str(doc.get("body", "")).splitlines()
                      if "seed:gap" not in line]
        visible = symbol and any(symbol in line for line in body_lines)
        if not symbol:
            faults.append({"path": path, "why": "doc row names no symbol"})
        if not outside:
            faults.append({"path": path,
                           "why": "no reader-use claim citing code outside the declaration"})
        if not visible:
            faults.append({"path": path,
                           "why": f"{symbol!r} never appears in the body outside a gap marker"})
    return gate_result(not faults, {"docs": len(docs), "faults": faults})


def score(text: str, manifest_path: Path, approve: int | None = None) -> dict[str, Any]:
    rows = load_rows(text)
    manifest = load_manifest(manifest_path)
    candidates = [row for row in rows if row.get("type") == "candidate"]
    claims = [row for row in rows if row.get("type") == "claim"]
    docs = [row for row in rows if row.get("type") == "doc"]
    coverage_rows = [row for row in rows if row.get("type") == "coverage"]
    inventories = [row for row in rows if row.get("type") == "inventory"]
    winnows = [row for row in rows if row.get("type") == "winnow"]
    judge_rows = [row for row in rows if row.get("type") == "judge"]
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
    gates["goal 1 — complete inventory"] = gate_inventory(
        text, inventories, candidates, manifest)
    gates["goal 2 — ranked walk, no cherry-picking"] = gate_walk(
        candidates, len(manifest))
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

    gates["goal 4 — seeded output winnowed"] = gate_winnow(winnows, len(claims))
    gates["goal 7 — the owner sets the write count"] = gate_owner(
        rows, docs, coverage_rows, by_symbol, approve)
    gates["goal 8 — reader-use and repeat visibility"] = gate_reader_use(
        docs, claims, by_symbol)
    inventory_judges = [row for row in judge_rows if row.get("stage") == "inventory"]
    doc_judges = [row for row in judge_rows if row.get("stage", "docs") == "docs"]
    written_doc_count = len({str(row.get("path", "")) for row in docs})
    gates["goal — second provider (inventory)"] = gate_judge(inventory_judges, 1)
    gates["goal — second provider (docs)"] = gate_judge(
        doc_judges, written_doc_count, enforce_docs_count=True)

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
    gates["goal 7 — coverage identity"] = gate_result(coverage_ok, coverage_detail)

    # The self-reported `lines` field is not trusted: the bound is enforced
    # against the doc's ACTUAL body content, and a missing/non-string body
    # (required by the prompt whenever a doc is emitted) fails outright.
    invalid_bounds: list[dict[str, Any]] = []
    for doc in docs:
        body = doc.get("body")
        if not isinstance(body, str) or not body.strip():
            invalid_bounds.append({**doc, "why": "missing or malformed body"})
            continue
        actual_lines = len(body.splitlines())
        if actual_lines > 60:
            invalid_bounds.append({**doc, "why": f"body is {actual_lines} lines, over 60",
                                   "actual_lines": actual_lines})
    gates["goal 8 — per-doc bound (<= 60 added lines)"] = gate_result(
        not invalid_bounds, {"invalid": invalid_bounds})

    # Goal 8, the two counts the per-doc bound leaves open: every gap marker and every
    # touched doc path belongs to a written doc, and neither count exceeds `written`.
    written_paths = {str(doc.get("path", "")) for doc in docs}
    gap_docs = [str(row.get("doc", "")) for row in [r for r in rows if r.get('type') == 'gap']]
    touched = set(gap_docs) | {str(row.get("doc", "")) for row in claims}
    stray = sorted(touched - written_paths)
    per_doc = collections.Counter(gap_docs)                  # the command allows one marker per written doc
    bounded = (len(gap_docs) <= len(docs) and len(touched) <= len(docs) and not stray
               and all(count <= 1 for count in per_doc.values()))
    gates["goal 8 — markers and doc paths bounded by written"] = gate_result(
        bounded, {"gap_markers": len(gap_docs), "markers_per_doc": dict(per_doc), "touched_doc_paths": len(touched),
                  "written": len(docs), "stray_paths": stray})

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
        print("unknown candidates (inventory gate failed):")
        for row in result["unknown_candidates"]:
            print(f"  {row.get('symbol')} rank {row.get('rank')}")
    if gate_status:
        print(f"baseline: {gate_status}")


# --- selftest ---------------------------------------------------------------

def _transcript(rows: list[dict[str, Any]], prose: str = "") -> str:
    return prose + "\n" + "\n".join(json.dumps(row) for row in rows) + "\n"


DECLARATIONS = {
    "with_backoff": "helpers.py:12", "resolve_setting": "config.py:8",
    "get_release_name": "helpers.py:31", "ordered_releases": "helpers.py:40",
    "render_release": "helpers.py:52", "load_config": "config.py:20",
    "sort_releases": "helpers.py:60", "format_row": "helpers.py:70",
    "build_parser": "cli.py:10", "main": "cli.py:80", "DEFAULT_TIMEOUT": "config.py:4",
}
WRITTEN = {
    "with_backoff": (
        "with_backoff retries only ConnectionError and TimeoutError, re-raising anything else.",
        "Callers in cli.py wrap every network call with it.", "cli.py:44"),
    "resolve_setting": (
        "resolve_setting resolves cli over env over file over default.",
        "The cli parser in cli.py passes its parsed flags straight to it.", "cli.py:22"),
}


def _passing_rows(manifest_path: Path) -> list[dict[str, Any]]:
    """A synthetic run that passes every gate against the real manifest."""
    manifest = load_manifest(manifest_path)
    order = [row for row in manifest if row["expected"] != "unscored"] + \
            [row for row in manifest if row["expected"] == "unscored"]
    rows: list[dict[str, Any]] = [{
        "type": "inventory", "source_files": 4, "scanned_files": 4,
        "candidates": len(manifest), "warnings": []}]
    for rank, row in enumerate(order, start=1):
        verdict = row["expected"] if row["expected"] != "unscored" else "not-worthy"
        rows.append({"type": "candidate", "symbol": row["symbol"], "kind": "function",
                     "code_ref": DECLARATIONS[row["symbol"]], "rank": rank,
                     "verdict": verdict, "reader_use": row["note"][:40]})
    worthy = [row["symbol"] for row in manifest if row["expected"] == "worthy"]
    rows.append({"type": "recommendation", "worthy": worthy, "recommend": len(worthy)})
    rows.append({"type": "owner", "chosen": len(worthy)})
    claims: list[dict[str, Any]] = []
    docs: list[dict[str, Any]] = []
    for symbol in worthy:
        path = f"eval/fixture/docs/{symbol}.md"
        declared, reader_use, ref = WRITTEN[symbol]
        claims.append({"type": "claim", "doc": path, "claim": declared,
                       "code_ref": DECLARATIONS[symbol], "kind": "declaration"})
        claims.append({"type": "claim", "doc": path, "claim": reader_use,
                       "code_ref": ref, "kind": "reader-use"})
        docs.append({"type": "doc", "path": path, "symbol": symbol, "lines": 12,
                     "body": f"# {symbol}\n\n{declared}\n{reader_use}\n"})
    rows += claims
    rows.append({"type": "winnow", "certified": len(claims), "drift": 0,
                 "unverified": 0})
    rows += docs
    verdicts = [str(row.get("verdict")) for row in rows
                if row.get("type") == "candidate"]
    rows.append({"type": "coverage", "N": len(manifest),
                 "documented": verdicts.count("documented"),
                 "worthy": verdicts.count("worthy"),
                 "not_worthy": verdicts.count("not-worthy"),
                 "deferred": verdicts.count("deferred"),
                 "written": len(docs)})
    rows.append({"type": "judge", "stage": "inventory", "status": "ok", "provider": "codex",
                 "docs": 4, "certified": 10, "drift": 0, "unverified": 0})
    rows.append({"type": "judge", "stage": "docs", "status": "ok", "provider": "codex",
                 "docs": len(docs), "certified": len(docs), "drift": 0,
                 "unverified": 0})
    return rows


def selftest(manifest_path: Path) -> int:
    """Known-pass and known-fail transcripts, one failing control per gate."""
    base_rows = _passing_rows(manifest_path)
    cases: list[tuple[str, list[dict[str, Any]], int | None, bool]] = [
        ("known-pass", base_rows, None, True),
        ("known-pass — the owner count given as --approve",
         [row for row in base_rows if row.get("type") != "owner"], 2, True),
    ]

    def mutate(label, fn, approve=None, want_pass=False):
        rows = [dict(row) for row in base_rows]
        cases.append((label, fn(rows), approve, want_pass))

    def find(rows, row_type, **match):
        return next(row for row in rows if row.get("type") == row_type
                    and all(row.get(key) == value for key, value in match.items()))

    mutate("goal 1 — no inventory row",
           lambda rows: [r for r in rows if r.get("type") != "inventory"])

    def scan_gap(rows):
        rows[0]["scanned_files"] = int(rows[0]["source_files"]) - 1
        return rows
    mutate("goal 1 — scanned fewer files than are in scope", scan_gap)

    def truncated(rows):
        rows[0]["warnings"] = ["candidate list truncated at 50"]
        return rows
    mutate("goal 1 — a truncated warning", truncated)

    def unquoted(rows):
        rows[0]["warnings"] = ["3 files outside inventory were skipped"]
        return rows
    mutate("goal 1 — an outside-inventory warning never quoted", unquoted)

    def no_kind(rows):
        find(rows, "candidate", rank=1).pop("kind")
        return rows
    mutate("goal 1 — a candidate row with no kind", no_kind)

    def no_declaration(rows):
        find(rows, "candidate", rank=2)["code_ref"] = "helpers.py"
        return rows
    mutate("goal 1 — a declaration with no line number", no_declaration)

    def rank_gap(rows):
        find(rows, "candidate", rank=4)["rank"] = 11
        return rows
    mutate("goal 2 — a skipped rank", rank_gap)

    def early_defer(rows):
        find(rows, "candidate", rank=3)["verdict"] = "deferred"
        return rows
    mutate("goal 2 — a deferred row inside the walk", early_defer)

    def winnow_drift(rows):
        find(rows, "winnow")["drift"] = 1
        return rows
    mutate("goal 4 — drift in the seeded output", winnow_drift)

    def no_winnow(rows):
        return [r for r in rows if r.get("type") != "winnow"]
    mutate("goal 4 — the seeded output was never winnowed", no_winnow)

    def drafts_first(rows):
        rec = find(rows, "recommendation")
        rows.remove(rec)
        rows.append(rec)
        return rows
    mutate("goal 7 — drafts before the worthy list", drafts_first)

    def no_owner(rows):
        return [r for r in rows if r.get("type") != "owner"]
    mutate("goal 7 — docs written with no owner answer", no_owner)

    def over_chosen(rows):
        find(rows, "owner")["chosen"] = 1
        return rows
    mutate("goal 7 — more written than the owner chose", over_chosen)

    def documented_written(rows):
        path = "eval/fixture/docs/render_release.md"
        rows.append({"type": "claim", "doc": path,
                     "claim": "render_release renders one release row.",
                     "code_ref": "helpers.py:52", "kind": "reader-use"})
        rows.append({"type": "doc", "path": path, "symbol": "render_release",
                     "lines": 8,
                     "body": "# render_release\n\nrender_release renders one release row.\n"})
        find(rows, "winnow")["certified"] = 5
        find(rows, "coverage")["written"] = 3
        return rows
    mutate("goal 7 — an already-documented candidate written anyway",
           documented_written)

    def no_reader_use(rows):
        for row in rows:
            if row.get("type") == "claim" and row.get("kind") == "reader-use":
                row["kind"] = "declaration"
        return rows
    mutate("goal 8 — no reader-use row on a written doc", no_reader_use)

    def declaration_only_reader_use(rows):
        row = find(rows, "claim", kind="reader-use",
                   doc="eval/fixture/docs/with_backoff.md")
        row["code_ref"] = DECLARATIONS["with_backoff"]
        return rows
    mutate("goal 8 — reader-use citing only the declaration line",
           declaration_only_reader_use)

    def symbol_absent(rows):
        doc = find(rows, "doc", symbol="with_backoff")
        doc["body"] = doc["body"].replace("with_backoff", "the helper")
        return rows
    mutate("goal 8 — the symbol never appears in the written doc", symbol_absent)

    def gap_filler(n: int) -> str:
        # seed:gap-marked lines are exempt from the speculation gate, so this
        # padding trips only the line-count bound -- every other gate about
        # this doc (reader-use, symbol visibility, claim ledger) stays intact.
        return "\n".join(f"<!-- seed:gap — filler line {i} -->" for i in range(n))

    def oversized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        doc = find(rows, "doc", symbol="with_backoff")
        doc["body"] = doc["body"] + gap_filler(60) + "\n"
        return rows
    mutate("goal 8 — a doc whose actual body is over 60 lines", oversized)

    def small_lines_field_big_body(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # The self-reported `lines` field lies small; only the real body counts.
        doc = find(rows, "doc", symbol="with_backoff")
        doc["lines"] = 5
        doc["body"] = doc["body"] + gap_filler(60) + "\n"
        return rows
    mutate("goal 8 — falsified body (small `lines` field, oversized actual body)",
           small_lines_field_big_body)

    def missing_body(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        del find(rows, "doc", symbol="with_backoff")["body"]
        return rows
    mutate("goal 8 — a doc with no body", missing_body)

    def duplicate_symbol(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        find(rows, "candidate", rank=2)["symbol"] = find(rows, "candidate", rank=1)["symbol"]
        return rows
    mutate("goal 1 — falsified identity (duplicate candidate symbol)", duplicate_symbol)

    def unknown_symbol(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        find(rows, "candidate", rank=1)["symbol"] = "not_in_the_manifest"
        return rows
    mutate("goal 1 — falsified identity (candidate symbol not in manifest)", unknown_symbol)

    def wrong_module(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        find(rows, "candidate", symbol="get_release_name")["code_ref"] = "config.py:999"
        return rows
    mutate("goal 1 — falsified identity (candidate in the wrong module)", wrong_module)

    def falsified_count(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Declared count and row count still agree with each other, just not
        # with the manifest: one candidate row was dropped, and the declared
        # count was quietly dropped to match it.
        rows[0]["candidates"] = int(rows[0]["candidates"]) - 1
        rows.remove(find(rows, "candidate", rank=11))
        return rows
    mutate("goal 1 — falsified count (fewer candidate rows than the manifest)",
           falsified_count)

    def judge_docs_count_mismatch(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        find(rows, "judge", stage="docs")["docs"] = \
            len([r for r in rows if r.get("type") == "doc"]) + 1
        return rows
    mutate("goal — second provider: judge doc count does not match written docs",
           judge_docs_count_mismatch)

    def extra_marker(rows):
        for note in ("why a second marker", "why a third marker"):
            rows.append({"type": "gap", "doc": "eval/fixture/docs/with_backoff.md",
                         "text": f"<!-- seed:gap — author: {note} -->"})
        return rows
    mutate("goal 8 — more than one gap marker on a written doc", extra_marker)

    def stray_path(rows):
        rows.append({"type": "gap", "doc": "eval/fixture/docs/never_written.md",
                     "text": "<!-- seed:gap — author: stray -->"})
        return rows
    mutate("goal 8 — a marker on a doc path that was never written", stray_path)

    def speculation(rows):
        doc = find(rows, "doc", symbol="with_backoff")
        doc["body"] += "It also retries on any HTTP 500 response.\n"
        return rows
    mutate("goal 5 — an unledgered sentence", speculation)

    def misclassified(rows):
        find(rows, "candidate", symbol="with_backoff")["verdict"] = "not-worthy"
        return rows
    mutate("classification — a worthy symbol declined", misclassified)

    def no_judge(rows):
        return [r for r in rows if not (r.get("type") == "judge" and r.get("stage") == "docs")]
    mutate("goal — second provider: docs written, no judge row", no_judge)

    def judge_abstain(rows):
        find(rows, "judge", stage="docs")["status"] = "abstain"
        find(rows, "judge", stage="docs")["reason"] = "codex unavailable"
        return rows
    mutate("goal — second provider: judge abstained", judge_abstain)

    def judge_drift(rows):
        find(rows, "judge", stage="docs")["drift"] = 1
        return rows
    mutate("goal — second provider: judge reports drift", judge_drift)

    def no_inventory_judge(rows):
        return [r for r in rows if not (r.get("type") == "judge" and r.get("stage") == "inventory")]
    mutate("goal — second provider: inventory never judged", no_inventory_judge)

    def inventory_judge_abstain(rows):
        find(rows, "judge", stage="inventory")["status"] = "abstain"
        find(rows, "judge", stage="inventory")["reason"] = "codex unavailable"
        return rows
    mutate("goal — second provider: inventory judge abstained", inventory_judge_abstain)

    def inventory_judge_drift(rows):
        find(rows, "judge", stage="inventory")["drift"] = 1
        return rows
    mutate("goal — second provider: inventory judge reports drift", inventory_judge_drift)

    ok = True
    for label, rows, approve, want_pass in cases:
        result = score(_transcript(rows), manifest_path, approve)
        got = result["passed"]
        failed = [name for name, gate in result["gates"].items() if not gate["pass"]]
        ok = ok and got == want_pass
        print(f"{'PASS' if got == want_pass else 'FAIL'} - {label}"
              f" (want {'pass' if want_pass else 'fail'},"
              f" got {'pass' if got else 'fail'})"
              + (f" [failing: {'; '.join(failed)}]" if failed else ""))
    print(f"selftest: {'PASS' if ok else 'FAIL'} ({len(cases)} transcripts)")
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_output", type=Path, nargs="?")
    parser.add_argument("--gate", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--manifest", type=Path, default=HERE / "manifest.tsv")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--approve", type=int, default=None,
                        help="the owner's chosen write count, when given at invocation")
    parser.add_argument("--selftest", action="store_true",
                        help="grade built-in pass/fail transcripts instead of a run")
    args = parser.parse_args()
    if args.selftest:
        return selftest(args.manifest)
    if args.run_output is None:
        parser.error("a run-output path is required (or --selftest)")
    try:
        text = args.run_output.read_text()
        result = score(text, args.manifest, args.approve)
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
