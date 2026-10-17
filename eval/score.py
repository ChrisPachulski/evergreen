#!/usr/bin/env python3
"""Score a headless winnow run against manifest.tsv and the frozen hard goals.

Usage:
    python3 score.py <run-output.txt> [--allow-misses N] [--json]
    python3 score.py --selftest

Two things are graded, and they are different things.

**The fixture exam** — recall over the 10 planted lies, false positives over the
8 true-claim decoys, and violations over the 2 exempt docs. This is the answer
key in `manifest.tsv`; it measures detection.

**The hard goals** — goals 1, 2, 3, 5 and 6 of
`skills/evergreen/hard-goals/winnow.md`, graded off the run's own ledger. This
measures whether the run was *done*, independent of what it found.

The run's trailing `jsonl` block carries the ledger. Non-JSON lines are ignored
so agent prose never makes the scorer operationally fail. Row types:

    {"type":"claims","n":18,"command":"grep -rnE ... README.md docs/"}
    {"type":"verdict","status":"certified","file":"README.md","line":12,
     "claim":"<the doc phrase>","code":"shipit/cli.py:31"}
    {"type":"verdict","status":"drift","file":"README.md","line":8,
     "claim":"<the doc phrase>","why":"gone; shipit/cli.py:1"}
    {"type":"verdict","status":"unverified","file":"docs/usage.md","line":22,
     "claim":"<the doc phrase>","why":"behavior-asserted — verify manually"}
    {"type":"left_alone","file":"CHANGELOG.md","reason":"history, frozen"}

Legacy `{"type":"flag",...}` rows are still read and count as `drift` verdicts,
so an older transcript still scores on the fixture exam — but a run that emits
only flags has no claim census and fails goals 1, 2 and 5, which is the point.

What each goal gate does and does not prove:
  goal 1  a claims row states N and the command that produced it. Presence and
          shape are checked; that the command really returns N is not re-run.
  goal 2  verdict rows == N. Counted exactly.
  goal 3  every certified row carries a code `path:line` that is not a doc path.
          Presence is checked; the citation is not opened.
  goal 4  NOT enforced here — proxied by the fixture token match (a drift row
          only scores recall when its claim quotes the planted token). Proving
          each drift by grep needs the fixture tree at the run's commit.
  goal 5  certified + drift + unverified == N, and no other status is allowed.
  goal 6  no exempt-doc path on a certified or drift row.

Exit codes: 0 = every gate and the fixture exam pass, 2 = any failure,
1 = nothing parseable in the transcript (an operational break, not a verdict).
"""
import argparse
import csv
import json
import posixpath
import re
from pathlib import Path

HERE = Path(__file__).parent
STATUSES = ("certified", "drift", "unverified")
CODE_REF_RE = re.compile(r"[\w./+-]+\.[A-Za-z0-9_]+:\d+")
DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt", ".adoc")
COMMAND_RE = re.compile(r"\b(grep|rg|git\s+grep|ag|ack|awk|sed|find)\b")
# Prose fallback for goal 1, for a run that reports the census in words:
#   "N = 18 documented claims" / "18 claims in scope"
PROSE_N_RE = re.compile(
    r"(?:\bN\s*=\s*(\d+)\b[^\n]{0,40}claims|\b(\d+)\s+(?:documented\s+)?claims\b"
    r"[^\n]{0,20}(?:in scope|enumerated|found))", re.I)


def load_output(text):
    """(claims_rows, verdict_rows, left_alone_rows, judge_rows) from a noisy transcript."""
    claims, verdicts, left, judge = [], [], [], []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type")
        if kind == "claims":
            claims.append(obj)
        elif kind == "verdict":
            verdicts.append(obj)
        elif kind == "flag":
            # Legacy row: a finding is a drift verdict by another name.
            verdicts.append({"type": "verdict", "status": "drift",
                             "file": obj.get("file"), "line": obj.get("line"),
                             "claim": obj.get("claim"), "why": obj.get("why"),
                             "legacy": True})
        elif kind == "left_alone":
            left.append(obj)
        elif kind == "judge":
            judge.append(obj)
    return claims, verdicts, left, judge


def fixture_relative(path):
    """A run's doc path, relative to the fixture root.

    run.sh hands the agent the fixture as its cwd, so paths arrive root-relative; a run made
    from the repo root prefixes `eval/fixture/`, and an absolute path runs through run.sh's
    `winnow-exam.*` sandbox. Both prefixes are cut. Nothing else is: a basename match would
    score `docs/README.md` as `README.md`.
    """
    parts = posixpath.normpath(str(path or "")).split("/")
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].startswith("winnow-exam.") or parts[i - 1:i + 1] == ["eval", "fixture"]:
            return "/".join(parts[i + 1:])
    return "/".join(parts)


def same_doc(run_path, manifest_path):
    return fixture_relative(run_path) == posixpath.normpath(manifest_path)


def code_ref(row):
    """The first code `path:line` on a verdict row, or None.

    A doc path never counts: goal 3 asks for the code that makes the claim
    true, and a README citing itself proves nothing.
    """
    for field in ("code", "code_ref", "evidence", "why"):
        for m in CODE_REF_RE.finditer(str(row.get(field, ""))):
            if not m.group(0).lower().split(":")[0].endswith(DOC_SUFFIXES):
                return m.group(0)
    return None


def prose_census(text):
    """(n, command) parsed from prose, for a run that did not emit a claims row."""
    m = PROSE_N_RE.search(text)
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    tail = text[m.end():m.end() + 400]
    head = text[max(0, m.start() - 400):m.start()]
    cmd = next((line.strip() for line in (tail + "\n" + head).splitlines()
                if COMMAND_RE.search(line)), None)
    return {"n": n, "command": cmd or "", "source": "prose"}


def grade_judge(judge_rows):
    """Second provider — exactly one judge row, status ok, at least one doc judged.

    An absent row, an abstain, or a row that judged no doc each fail: a second-provider pass is
    not optional evidence, it is the thing this goal exists to require. The judge's drift count is
    reported, never gated: this fixture seeds drift on purpose, so a judge that finds some is doing
    its job. Zero judge drift is the release gate's rule, for real docs.
    """
    if len(judge_rows) != 1:
        return {"pass": False, "rows": judge_rows,
                "why": f"expected exactly one judge row, saw {len(judge_rows)}"}
    row = judge_rows[0]
    status = row.get("status")
    if status != "ok":
        reason = row.get("reason")
        return {"pass": False, "status": status, "reason": reason,
                "provider": row.get("provider"),
                "why": f"judge status is {status!r}, not \"ok\""}
    docs = row.get("docs")
    ok = isinstance(docs, int) and not isinstance(docs, bool) and docs >= 1
    return {"pass": ok, "status": status, "provider": row.get("provider"),
            "docs": docs, "certified": row.get("certified"),
            "drift": row.get("drift"), "unverified": row.get("unverified"),
            "why": "ok" if ok else f"judge reports docs={docs!r}; at least one doc must be judged"}


def grade_goals(text, claims_rows, verdicts, judge_rows):
    """The five transcript-checkable winnow hard goals. -> (rows, all_ok)."""
    census = claims_rows[0] if claims_rows else prose_census(text)
    n = None
    if census is not None:
        try:
            n = int(census.get("n"))
        except (TypeError, ValueError):
            n = None
    command = str((census or {}).get("command", "")).strip()

    goal1 = (n is not None and n > 0 and bool(COMMAND_RE.search(command))
             and len(claims_rows) <= 1)
    goal1_why = ("ok" if goal1 else
                 "no claim census (a claims row, or 'N = <int> claims' with its command)"
                 if census is None else
                 "more than one claims row" if len(claims_rows) > 1 else
                 f"census command is not a countable command: {command!r}")

    by_status = {s: [v for v in verdicts if str(v.get("status")) == s]
                 for s in STATUSES}
    unknown = [v for v in verdicts if str(v.get("status")) not in STATUSES]

    goal2 = n is not None and len(verdicts) == n
    uncited = [v for v in by_status["certified"] if code_ref(v) is None]
    goal3 = bool(by_status["certified"]) and not uncited
    total = sum(len(rows) for rows in by_status.values())
    goal5 = n is not None and total == n and not unknown

    return {
        "goal 1 — claim census with its command": {
            "pass": goal1, "n": n, "command": command, "why": goal1_why},
        "goal 2 — one verdict row per claim": {
            "pass": goal2, "rows": len(verdicts), "n": n},
        "goal 3 — every certified row cites code": {
            "pass": goal3, "certified": len(by_status["certified"]),
            "uncited": [v.get("claim") for v in uncited]},
        "goal 5 — the three counts sum to N": {
            "pass": goal5, "n": n, "certified": len(by_status["certified"]),
            "drift": len(by_status["drift"]),
            "unverified": len(by_status["unverified"]),
            "unknown_status": [str(v.get("status")) for v in unknown]},
        "goal — second provider": grade_judge(judge_rows),
    }, by_status


def score(text, manifest_path, allow_misses=0):
    claims_rows, verdicts, left, judge_rows = load_output(text)
    goals, by_status = grade_goals(text, claims_rows, verdicts, judge_rows)
    drift_rows = by_status["drift"]
    certified_rows = by_status["certified"]

    rows = list(csv.DictReader(open(manifest_path), delimiter="\t"))
    drift = [r for r in rows if r["kind"] == "drift"]
    decoys = [r for r in rows if r["kind"] == "decoy"]
    exempt = [r for r in rows if r["kind"] == "exempt"]

    def hit(row, f):
        return same_doc(f.get("file"), row["file"]) and \
            row["token"].lower() in str(f.get("claim", "")).lower()

    def is_exempt(f):
        return any(same_doc(f.get("file"), r["file"]) for r in exempt)

    caught = [r for r in drift if any(hit(r, f) for f in drift_rows)]
    missed = [r for r in drift if r not in caught]
    fps = [r for r in decoys if any(hit(r, f) for f in drift_rows)]
    violations = [f for f in drift_rows + certified_rows if is_exempt(f)]
    unmatched = [f for f in drift_rows
                 if not any(hit(r, f) for r in drift + decoys) and not is_exempt(f)]
    honored = [r for r in exempt if any(same_doc(x.get("file"), r["file"]) for x in left)]
    true_flags = [f for f in drift_rows if any(hit(r, f) for r in drift)]

    goals["goal 6 — exempt docs stay out of findings"] = {
        "pass": not violations,
        "violations": [f"{f.get('file')}:{f.get('line')}" for f in violations]}
    exam = {
        "recall": {"caught": len(caught), "total": len(drift),
                   "missed": [r["id"] for r in missed],
                   "pass": len(missed) <= allow_misses},
        "decoy_false_positives": {"count": len(fps),
                                  "ids": [r["id"] for r in fps],
                                  "pass": not fps},
        "exempt_left_alone": {"honored": len(honored), "total": len(exempt)},
        "precision": (len(true_flags) / len(drift_rows)) if drift_rows else None,
        "unmatched_flags": [{"file": f.get("file"), "line": f.get("line"),
                             "claim": f.get("claim")} for f in unmatched],
        "caught_ids": [r["id"] for r in caught],
    }
    parseable = bool(verdicts or left or claims_rows)
    passed = (parseable and all(g["pass"] for g in goals.values())
              and exam["recall"]["pass"] and exam["decoy_false_positives"]["pass"])
    return {"parseable": parseable, "goals": goals, "exam": exam,
            "allow_misses": allow_misses, "passed": passed,
            "manifest_rows": len(rows)}


def render(result):
    exam = result["exam"]
    rec = exam["recall"]
    print(f"drift caught (recall):   {rec['caught']}/{rec['total']}"
          f"  — missed: {', '.join(rec['missed']) or 'none'}")
    for cid in exam["caught_ids"]:
        print(f"  ✓ {cid}")
    fp = exam["decoy_false_positives"]
    print(f"decoy false positives:   {fp['count']}  — {', '.join(fp['ids']) or 'none'}")
    left = exam["exempt_left_alone"]
    print(f"exempt explicitly left alone: {left['honored']}/{left['total']}")
    if exam["unmatched_flags"]:
        print(f"unseeded flags (audit these by hand): {len(exam['unmatched_flags'])}")
        for f in exam["unmatched_flags"]:
            print(f"  ? {f['file']}:{f['line']} — {f['claim']}")
    if exam["precision"] is not None:
        print(f"precision (drift rows matching seeded drift / all drift rows):"
              f" {exam['precision']:.2f}")
    if rec["total"]:
        print(f"recall: {rec['caught'] / rec['total']:.2f}")
    print("--- hard goals (skills/evergreen/hard-goals/winnow.md) ---")
    for name, gate in result["goals"].items():
        print(f"{name}: {'PASS' if gate['pass'] else 'FAIL'}")
        if not gate["pass"]:
            detail = {k: v for k, v in gate.items() if k != "pass"}
            print(f"  {json.dumps(detail, sort_keys=True)}")
    print("goal 4 — every drift proven gone: PROXIED"
          " (fixture token match; not re-grepped here)")
    print(f"verdict: {'PASS' if result['passed'] else 'FAIL'}")


# --- selftest ---------------------------------------------------------------

def _transcript(rows, prose=""):
    return prose + "\n" + "\n".join(json.dumps(r) for r in rows) + "\n"


def _passing_rows(manifest_path):
    """A synthetic transcript that passes every gate against the real manifest."""
    rows = list(csv.DictReader(open(manifest_path), delimiter="\t"))
    drift = [r for r in rows if r["kind"] == "drift"]
    decoys = [r for r in rows if r["kind"] == "decoy"]
    exempt = [r for r in rows if r["kind"] == "exempt"]
    out = [{"type": "verdict", "status": "drift", "file": r["file"], "line": 10 + i,
            "claim": f"the doc says {r['token']} here",
            "why": f"gone from the code; shipit/cli.py:{20 + i}"}
           for i, r in enumerate(drift)]
    out += [{"type": "verdict", "status": "certified", "file": r["file"],
             "line": 30 + i, "claim": f"the doc says {r['token']} here",
             "code": f"shipit/cli.py:{40 + i}"}
            for i, r in enumerate(decoys)]
    out += [{"type": "verdict", "status": "unverified", "file": "docs/usage.md",
             "line": 60, "claim": "releases come back newest-first",
             "why": "behavior-asserted — verify manually"}]
    out += [{"type": "left_alone", "file": r["file"], "reason": r["note"]}
            for r in exempt]
    out += [{"type": "judge", "status": "ok", "provider": "codex",
             "docs": 2, "certified": 2, "drift": 0, "unverified": 0}]
    census = {"type": "claims", "n": len(drift) + len(decoys) + 1,
              "command": "grep -rnE '`[^`]+`' README.md docs/"}
    return [census] + out


def selftest(manifest_path):
    """Known-pass and known-fail transcripts, one failing control per gate."""
    base_rows = _passing_rows(manifest_path)
    # The prose fallback: the same ledger with the census stated in words.
    prose = (f"N = {base_rows[0]['n']} documented claims in scope, enumerated with\n"
             f"    {base_rows[0]['command']}\n")
    cases = [
        ("known-pass", base_rows, "", True),
        ("known-pass — census in prose, not a row",
         [r for r in base_rows if r["type"] != "claims"], prose, True),
    ]

    def mutate(label, fn):
        rows = [dict(r) for r in base_rows]
        cases.append((label, fn(rows), "", False))

    mutate("goal 1 — census row removed",
           lambda rows: [r for r in rows if r["type"] != "claims"])

    def bad_command(rows):
        rows[0]["command"] = "I counted them by reading"
        return rows
    mutate("goal 1 — census without a countable command", bad_command)

    def wrong_n(rows):
        rows[0]["n"] = int(rows[0]["n"]) + 1
        return rows
    mutate("goal 2 — rows do not equal N", wrong_n)

    def uncited_certified(rows):
        for r in rows:
            if r.get("status") == "certified":
                r.pop("code", None)
                break
        return rows
    mutate("goal 3 — a certified row with no code path:line", uncited_certified)

    def doc_cited_certified(rows):
        for r in rows:
            if r.get("status") == "certified":
                r["code"] = "README.md:17"
                break
        return rows
    mutate("goal 3 — a certified row citing a doc, not code", doc_cited_certified)

    def stray_status(rows):
        for r in rows:
            if r.get("status") == "unverified":
                r["status"] = "skipped"
                break
        return rows
    mutate("goal 5 — a status outside the three", stray_status)

    def exempt_flagged(rows):
        rows.append({"type": "verdict", "status": "drift",
                     "file": "docs/adr/0001-worker-pool.md", "line": 4,
                     "claim": "--workers sizes the pool",
                     "why": "flag on an ADR; shipit/cli.py:12"})
        rows[0]["n"] = int(rows[0]["n"]) + 1
        return rows
    mutate("goal 6 — an exempt doc on a drift row", exempt_flagged)

    mutate("goal — second provider: no judge row",
           lambda rows: [r for r in rows if r["type"] != "judge"])

    def judge_abstain(rows):
        for r in rows:
            if r["type"] == "judge":
                r["status"] = "abstain"
                r["reason"] = "codex unavailable"
        return rows
    mutate("goal — second provider: judge abstained", judge_abstain)

    def judge_no_docs(rows):
        for r in rows:
            if r["type"] == "judge":
                r["docs"] = 0
        return rows
    mutate("goal — second provider: judge row judged no doc", judge_no_docs)

    def missed_drift(rows):
        out, dropped = [], False
        for r in rows:
            if not dropped and r.get("status") == "drift":
                dropped = True
                continue
            out.append(r)
        out[0]["n"] = int(out[0]["n"]) - 1
        return out
    mutate("fixture exam — a planted lie missed", missed_drift)

    def decoy_flagged(rows):
        rows.append({"type": "verdict", "status": "drift", "file": "README.md",
                     "line": 9, "claim": "--retries is documented here",
                     "why": "wrong; shipit/cli.py:12"})
        rows[0]["n"] = int(rows[0]["n"]) + 1
        return rows
    mutate("fixture exam — a true decoy flagged", decoy_flagged)

    def same_name_other_dir(rows):
        for r in rows:
            if r.get("status") == "drift" and r["file"] == "README.md":
                r["file"] = "docs/README.md"
                break
        return rows
    mutate("fixture exam — a drift row on docs/README.md does not catch README.md",
           same_name_other_dir)

    rows = [dict(r) for r in base_rows]
    rows.append({"type": "verdict", "status": "certified",
                 "file": "vendor/CHANGELOG.md", "line": 3,
                 "claim": "the vendored changelog lists 1.0", "code": "shipit/cli.py:12"})
    rows[0]["n"] = int(rows[0]["n"]) + 1
    cases.append(("known-pass — vendor/CHANGELOG.md is not the exempt CHANGELOG.md",
                  rows, "", True))

    ok = True
    for label, rows, prose_text, want_pass in cases:
        result = score(_transcript(rows, prose_text), manifest_path)
        got = result["passed"]
        failed = [name for name, gate in result["goals"].items() if not gate["pass"]]
        failed += [name for name in ("recall", "decoy_false_positives")
                   if not result["exam"][name]["pass"]]
        ok = ok and got == want_pass
        print(f"{'PASS' if got == want_pass else 'FAIL'} - {label}"
              f" (want {'pass' if want_pass else 'fail'},"
              f" got {'pass' if got else 'fail'})"
              + (f" [failing: {'; '.join(failed)}]" if failed else ""))
    print(f"selftest: {'PASS' if ok else 'FAIL'} ({len(cases)} transcripts)")
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("run_output", nargs="?", type=Path)
    ap.add_argument("--manifest", type=Path, default=HERE / "manifest.tsv")
    ap.add_argument("--allow-misses", type=int, default=0,
                    help="planted lies the run may miss and still pass (default 0)")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--selftest", action="store_true",
                    help="grade built-in pass/fail transcripts instead of a run")
    args = ap.parse_args()
    if args.selftest:
        return selftest(args.manifest)
    if args.run_output is None:
        ap.error("a run-output path is required (or --selftest)")
    result = score(args.run_output.read_text(), args.manifest, args.allow_misses)
    if args.as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        render(result)
    if not result["parseable"]:
        return 1  # nothing to grade: operational break
    return 0 if result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
