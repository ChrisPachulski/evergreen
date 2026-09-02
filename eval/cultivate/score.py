#!/usr/bin/env python3
"""Score a read-only `/evergreen:cultivate` run against a disposable Git fixture.

Usage:
    python3 score.py <run-output.txt> [--gate] [--json] [--manifest PATH]
                     [--write-baseline]
    python3 score.py --selftest

This scorer is pure stdlib and makes zero model calls. It tolerantly reads JSON
objects from the run's trailing JSONL block: prose and malformed lines are
ignored. The manifest is the fixture inventory; every planted path must receive
the expected verdict. A decoy dynamically loaded by `plugin_registry.py` is
reported first: proposing `orphan` or `delete-proposed` for it is the expensive
false positive this exam is designed to catch.

The gates cover the fixture-side branches of all seven cultivate hard goals:
inventory counts and the untracked gap; a reference-graph result behind every
verdict; no bare clean conclusion plus a stated non-coverage list;
assertion-backed tested claims; a specific not-checked list; and an
unresolvable file that stays `unsettled` with a named resolver. Goal 4 grades
only its fallback here: the fixture has no remote, so a passing run reports
`gh_ran: false` and `visibility: unchecked`. The live-remote branch of goal 4
is not covered by this exam and would require a throwaway public repository.

Goal 2 wants the grep RUN, so a verdict's `evidence` must carry a `git grep`
command AND its captured result — a count (`3 hits`), an explicit zero
(`0 hits` / `no hits`), or a path list. The bare words `git grep`, or a command
followed by prose, are rejected: both are what a run that skipped the pass
writes. The scorer still cannot re-run the graph, prove a cited line actually
asserts, assess dynamic references other than the planted decoy, or validate
the live-remote `gh` path. `--selftest` grades built-in pass/fail transcripts,
one failing control per gate. `--gate` compares 0/1 gate scores against
`baseline.json`; `--write-baseline` writes it only when explicitly requested.
Exit codes: 0 pass, 2 gate fail, 1 operational error.
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


SETTLED_VERDICTS = {"keep", "orphan", "untrack", "ignore", "delete-proposed"}
# Goal 2 wants the reference graph RUN, so evidence is a command AND its captured
# result. `git grep` as a bare phrase is a claim that one was run, which is what a
# run that skipped the pass also writes.
GIT_GREP_RE = re.compile(r"\bgit\s+grep\b", re.I)
EVIDENCE_SPLIT_RE = re.compile(r"\n|;|->|=>")
GREP_RESULT_RE = re.compile(
    r"(?i)^(?:\d+\s*(?:hits?|matches?|results?|references?|lines?)?\b"  # 3 hits, 0
    r"|(?:no|zero)\s+(?:hits?|matches?|results?|references?)"           # no hits
    r"|[\w./+-]+\.[A-Za-z0-9_]+(?::\d+)?)")                             # path list
# What would settle it: an env/config variable, or a concrete file that holds the answer.
RESOLVER_RE = re.compile(r"[A-Z][A-Z0-9_]{3,}|[\w./-]+\.(?:py|json|toml|cfg|ini|ya?ml|env)\b")


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


def evidence_rejection(evidence: object) -> str | None:
    """None if the evidence is a `git grep` AND its captured result, else why not.

    A result is a count (`3 hits`, `0`), an explicit zero (`no hits`), or a path
    list (`toolbox/cli.py:12, ...`). Prose after the command is not a result:
    the whole point of goal 2 is that the grep was executed and its output kept.
    """
    if not isinstance(evidence, str) or not evidence.strip():
        return "no evidence"
    if not GIT_GREP_RE.search(evidence):
        return "no git grep command"
    parts = [p.strip() for p in EVIDENCE_SPLIT_RE.split(evidence) if p.strip()]
    start = next((i for i, p in enumerate(parts) if GIT_GREP_RE.search(p)), None)
    for part in parts[start + 1:]:
        if GIT_GREP_RE.search(part):
            continue  # a second command, not its result
        if GREP_RESULT_RE.match(part):
            return None
    return "git grep named, but no captured result (a count, a path list, or 0 hits)"


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

    invalid_evidence = [{"path": str(row.get("path")),
                         "evidence": str(row.get("evidence", "")),
                         "why": evidence_rejection(row.get("evidence"))}
                        for row in verdicts
                        if evidence_rejection(row.get("evidence")) is not None]
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

    # goal 7 — an unresolvable reference must not become a verdict in either direction.
    unresolvable = {row["path"] for row in manifest if row["kind"] == "unresolvable"}
    forced_unresolvable = [
        {"path": path, "verdict": str(by_path[path].get("verdict"))}
        for path in sorted(unresolvable)
        if path in by_path and str(by_path[path].get("verdict")) in SETTLED_VERDICTS
    ]
    # An unsettled row has to name what WOULD settle it -- an env var, a config file, a table --
    # otherwise "unsettled" degrades into a shrug that hides the same omission a silent keep does.
    vague_unsettled = [
        {"path": str(row.get("path")), "evidence": str(row.get("evidence", ""))}
        for row in verdicts if str(row.get("verdict")) == "unsettled"
        and not RESOLVER_RE.search(str(row.get("evidence", "")))
    ]
    unsettled_ok = not forced_unresolvable and not vague_unsettled

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
        "goal 7 — unresolvable stays unsettled": gate_result(
            unsettled_ok, {"forced_into_a_verdict": forced_unresolvable,
                           "unsettled_without_a_resolver": vague_unsettled}),
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


# --- selftest ---------------------------------------------------------------

def _transcript(rows: list[dict[str, Any]], prose: str = "") -> str:
    return prose + "\n" + "\n".join(json.dumps(row) for row in rows) + "\n"


def _passing_rows(manifest_path: Path) -> list[dict[str, Any]]:
    """A synthetic run that passes every gate against the real manifest."""
    manifest = load_manifest(manifest_path)
    rows: list[dict[str, Any]] = [{
        "type": "inventory",
        "tracked": sum(row["tracked"] == "yes" for row in manifest),
        "on_disk": len(manifest),
        "gap_accounted": sum(row["tracked"] == "no" for row in manifest),
    }]
    for row in manifest:
        stem = Path(row["path"]).stem
        if row["expected"] == "unsettled":
            evidence = (f"git grep -nF {stem} -- .\n0 hits\n"
                        "TOOLBELT_HANDLER in toolbox/dispatch.py:14 would settle it")
        else:
            evidence = f"git grep -nF {stem} -- .\n2 hits: toolbox/cli.py:12"
        rows.append({"type": "verdict", "path": row["path"],
                     "verdict": row["expected"], "evidence": evidence})
    rows.append({"type": "exposure", "gh_ran": False, "visibility": "unchecked"})
    rows.append({"type": "tested", "element": "normalize_title", "status": "gap",
                 "test_ref": None})
    rows.append({"type": "tested", "element": "run_export", "status": "gap",
                 "test_ref": None})
    rows.append({"type": "not_checked",
                 "items": ["toolbox/plugins/adapter_runtime.py runtime behaviour",
                           "the live gh remote path"]})
    return rows


def selftest(manifest_path: Path) -> int:
    """Known-pass and known-fail transcripts, one failing control per gate."""
    base_rows = _passing_rows(manifest_path)
    cases: list[tuple[str, list[dict[str, Any]], str, bool]] = [
        ("known-pass", base_rows, "", True)]

    def mutate(label, fn, prose=""):
        rows = [dict(row) for row in base_rows]
        cases.append((label, fn(rows), prose, False))

    def first_verdict(rows, path):
        return next(row for row in rows
                    if row.get("type") == "verdict" and row["path"] == path)

    def bad_inventory(rows):
        rows[0]["tracked"] = int(rows[0]["tracked"]) - 1
        return rows
    mutate("goal 1 — tracked count off by one", bad_inventory)

    def bare_words(rows):
        first_verdict(rows, "toolbox/cli.py")["evidence"] = "git grep"
        return rows
    mutate("goal 2 — evidence is the bare words 'git grep'", bare_words)

    def prose_result(rows):
        first_verdict(rows, "toolbox/cli.py")["evidence"] = (
            "git grep -nF cli -- .; found it in the dispatch table")
        return rows
    mutate("goal 2 — a git grep with no captured result", prose_result)

    def no_command(rows):
        first_verdict(rows, "toolbox/cli.py")["evidence"] = "3 hits"
        return rows
    mutate("goal 2 — a result with no command", no_command)

    mutate("goal 3 — a bare clean conclusion", lambda rows: rows,
           prose="Verdict: clean\n")

    def drop_not_checked(rows):
        return [row for row in rows if row.get("type") != "not_checked"]
    mutate("goals 3 and 6 — no not-checked list", drop_not_checked)

    def vague_not_checked(rows):
        for row in rows:
            if row.get("type") == "not_checked":
                row["items"] = ["everything"]
        return rows
    mutate("goal 6 — a vague not-checked list", vague_not_checked)

    def assumed_visibility(rows):
        for row in rows:
            if row.get("type") == "exposure":
                row.update({"gh_ran": True, "visibility": "private"})
        return rows
    mutate("goal 4 — visibility assumed without gh output", assumed_visibility)

    def assertion_free(rows):
        rows.append({"type": "tested", "element": "normalize_title",
                     "status": "tested", "test_ref": "tests/test_runner.py:8"})
        return rows
    mutate("goal 5 — tested claim on an assertion-free test", assertion_free)

    def forced_keep(rows):
        first_verdict(rows, "toolbox/handlers/legacy_export.py")["verdict"] = "keep"
        return rows
    mutate("goal 7 — the unresolvable file forced into keep", forced_keep)

    def vague_unsettled(rows):
        first_verdict(rows, "toolbox/handlers/legacy_export.py")["evidence"] = (
            "git grep -nF legacy_export -- .\n0 hits")
        return rows
    mutate("goal 7 — unsettled with no named resolver", vague_unsettled)

    def decoy_deleted(rows):
        first_verdict(rows, "toolbox/plugins/adapter_runtime.py")["verdict"] = \
            "delete-proposed"
        return rows
    mutate("classification — the decoy proposed for deletion", decoy_deleted)

    ok = True
    for label, rows, prose, want_pass in cases:
        result = score(_transcript(rows, prose), manifest_path)
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
    parser.add_argument("--selftest", action="store_true",
                        help="grade built-in pass/fail transcripts instead of a run")
    args = parser.parse_args()
    if args.selftest:
        return selftest(args.manifest)
    if args.run_output is None:
        parser.error("a run-output path is required (or --selftest)")
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
