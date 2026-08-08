#!/usr/bin/env bash
# The local mirror of the CI test job. Run this before claiming a change is green.
#
# Why this exists: the Python tests are only one of four suites CI runs. On 2026-08-08 a
# README restructure passed `pytest tests/` and broke 36 assertions in tests/hooks.sh, which
# no Python runner touches; the same session then shipped a schema change that broke
# tests/action.sh the same way. Both reached main. "pytest passed" is not "green", and the
# only durable fix is one command that cannot forget a suite.
#
# Mirrors .github/workflows/test.yml (the ubuntu/macos Python 3.11 job) step for step.
# Every suite runs even if an earlier one fails, so one pass reports every break rather
# than hiding the rest behind the first.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2
PY="${PYTHON:-python3}"

# Colour only on a terminal. Piped or redirected output stays plain text so the status
# markers below are greppable -- a coloured "PASS" does not match a ^PASS pattern, which
# is its own way of lying about a result.
if [ -t 1 ]; then B=$'\033[1m'; R=$'\033[31m'; G=$'\033[32m'; N=$'\033[0m'
else B=""; R=""; G=""; N=""; fi

FAILED=""
run() {
  local name="$1"; shift
  printf '\n%s=== %s ===%s\n' "$B" "$name" "$N"
  if "$@"; then
    printf '%sPASS%s - %s\n' "$G" "$N" "$name"
  else
    printf '%sFAIL%s - %s\n' "$R" "$N" "$name"
    FAILED="$FAILED
  - $name"
  fi
}

run "Python tests"       "$PY" -m unittest discover -s tests -p 'test_*.py'
run "Hook integration"   bash tests/hooks.sh
run "Action integration" bash tests/action.sh
run "Benchmark self-test" "$PY" eval/bench/run_bench.py --selftest

printf '\n%s=== verdict ===%s\n' "$B" "$N"
if [ -n "$FAILED" ]; then
  printf '%sNOT GREEN%s - failing suites:%s\n' "$R" "$N" "$FAILED"
  printf 'A change is not done while any suite above fails.\n'
  exit 1
fi
printf '%sGREEN%s - all four CI suites pass locally.\n' "$G" "$N"
printf 'This mirrors the CI test job; it does not run the oracle or public-benchmark jobs.\n'
exit 0
