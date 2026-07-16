#!/usr/bin/env bash
# One command, one verdict: is this tree ready for public scrutiny?
# Runs every mechanical proof the repo ships and prints PASS/FAIL/SKIP per axis,
# then READY / NOT-READY (any FAIL = NOT-READY; SKIPs are named, never fatal).
# --fast swaps the full pytest run for the focused bench/flourish/oracle set.
# No network, no model calls, no mutation. Run: bash tests/readiness.sh [--fast]
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1
FAST=false
[ "${1:-}" = "--fast" ] && FAST=true

fails=0
skips=0
pass(){ printf 'PASS - %s\n' "$1"; }
fail(){ printf 'FAIL - %s%s\n' "$1" "${2:+ ($2)}"; fails=$((fails+1)); }
skip(){ printf 'SKIP - %s (%s)\n' "$1" "$2"; skips=$((skips+1)); }

# --- axis: test suite -------------------------------------------------------
# CI's own command (workflows/test.yml runs unittest discover, not pytest).
if $FAST; then
  if python3 -m unittest tests.test_bench tests.test_bench_resolver \
      tests.test_bench_artifact tests.test_flourish_score \
      tests.test_oracle_build >/dev/null 2>&1; then
    pass "focused test set (bench, flourish, oracle)"
  else
    fail "focused test set" "rerun without --fast for the full picture"
  fi
else
  if python3 -m unittest discover -s tests -p 'test_*.py' >/dev/null 2>&1; then
    pass "full test suite (CI's unittest discover)"
  else
    fail "full test suite" "python3 -m unittest discover -s tests -p 'test_*.py'"
  fi
fi

# --- axis: hooks ------------------------------------------------------------
if bash tests/hooks.sh >/dev/null 2>&1; then
  pass "hooks suite (guard, activate, digest agreement)"
else
  fail "hooks suite" "bash tests/hooks.sh"
fi

# --- axis: benchmark scoring math -------------------------------------------
if python3 eval/bench/run_bench.py --selftest >/dev/null 2>&1; then
  pass "benchmark scoring selftest"
else
  fail "benchmark scoring selftest" "python3 eval/bench/run_bench.py --selftest"
fi

# --- axis: 0.4.0 publication chain ------------------------------------------
if python3 eval/bench/publication.py verify \
    --manifest eval/bench/public/0.4.0/manifest.json \
    --repo . --report eval/bench/results-0.4.0.md >/dev/null 2>&1; then
  pass "0.4.0 publication verifies offline"
else
  fail "0.4.0 publication chain" "eval/bench/publication.py verify"
fi

# --- axis: flourish golden matrix -------------------------------------------
flourish_ok=true
flourish_graded=0
for fixture in eval/flourish/fixtures/*/; do
  for golden in "$fixture"golden/*.md; do
    [ -f "$golden" ] || continue
    case "$(basename "$golden")" in
      good.md|flattened.md) want=0 ;;
      gutted.md|fabricated.md|skeleton.md) want=2 ;;
      *) continue ;;
    esac
    python3 eval/flourish/score.py --fixture "$fixture" --result "$golden" \
      >/dev/null 2>&1
    got=$?
    flourish_graded=$((flourish_graded+1))
    if [ "$got" != "$want" ]; then
      flourish_ok=false
      printf '       golden %s: exit %s, want %s\n' "$golden" "$got" "$want"
    fi
  done
done
# An empty glob must never read as a pass — zero goldens graded is a failure.
if $flourish_ok && [ "$flourish_graded" -gt 0 ]; then
  pass "flourish golden matrix ($flourish_graded goldens, every trap trips its gate)"
else
  fail "flourish golden matrix" "graded $flourish_graded"
fi

# --- axis: oracle provenance contract (CI's offline job) ---------------------
if python3 -m eval.oracle.build validate-provenance \
    --manifest eval/oracle/sources/provenance.json --contract-only \
    >/dev/null 2>&1; then
  pass "oracle provenance contract validates offline"
else
  fail "oracle provenance contract" "python3 -m eval.oracle.build validate-provenance --contract-only"
fi

# --- axis: v2 split manifest (needs the external datasets) -------------------
V2_DATA="$HOME/evergreen-benchmark-data"
if [ -f "$V2_DATA/cascade-java-v2-dev.jsonl" ] && \
   [ -f "$V2_DATA/cascade-java-v2-holdout.jsonl" ]; then
  if python3 eval/bench/split_manifest.py \
      eval/bench/cascade-java-v2-split-manifest.json \
      "$V2_DATA/cascade-java-v2-dev.jsonl" \
      "$V2_DATA/cascade-java-v2-holdout.jsonl" >/dev/null 2>&1; then
    pass "v2 split manifest binds the external datasets"
  else
    fail "v2 split manifest" "hash mismatch against $V2_DATA"
  fi
else
  skip "v2 split manifest" "external datasets absent; regenerable per eval/bench/README.md"
fi

# --- info: tree state (not a gate — the verdict describes this exact tree) ---
dirty="$(git status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
head="$(git rev-parse --short HEAD 2>/dev/null)"
printf 'INFO - tree: HEAD %s, %s uncommitted change(s)\n' "$head" "$dirty"

echo
if [ "$fails" -eq 0 ]; then
  printf 'READY — every mechanical proof passed (%s skipped, named above)\n' "$skips"
  exit 0
fi
printf 'NOT-READY — %s axis(es) failed\n' "$fails"
exit 1
