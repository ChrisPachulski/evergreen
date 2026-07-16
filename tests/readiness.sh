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
if $FAST; then
  if python3 -m pytest tests/test_bench.py tests/test_bench_resolver.py \
      tests/test_bench_artifact.py tests/test_flourish_score.py \
      tests/test_oracle_build.py -q >/dev/null 2>&1; then
    pass "focused pytest set (bench, flourish, oracle)"
  else
    fail "focused pytest set" "rerun without --fast for the full picture"
  fi
else
  if python3 -m pytest tests/ -q >/dev/null 2>&1; then
    pass "full pytest suite"
  else
    fail "full pytest suite" "python3 -m pytest tests/ -q"
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
for fixture in eval/flourish/fixtures/*/; do
  for golden in "$fixture"golden/*.md; do
    case "$(basename "$golden")" in
      good.md|flattened.md) want=0 ;;
      gutted.md|fabricated.md|skeleton.md) want=2 ;;
      *) continue ;;
    esac
    python3 eval/flourish/score.py --fixture "$fixture" --result "$golden" \
      >/dev/null 2>&1
    got=$?
    if [ "$got" != "$want" ]; then
      flourish_ok=false
      printf '       golden %s: exit %s, want %s\n' "$golden" "$got" "$want"
    fi
  done
done
$flourish_ok && pass "flourish golden matrix (every trap trips its gate)" \
  || fail "flourish golden matrix"

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
