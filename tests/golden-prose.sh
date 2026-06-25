#!/usr/bin/env bash
# golden-prose — LLM-free scoring of the --fix-prose model fixer (the prose half of
# "keep fresh"). The SUBJECT is a live model call (non-deterministic); the RUBRIC is
# deterministic: build a doc with a dead-path reference + an unrelated sentinel line, run
# --fix-prose, then score: (1) the dead-path finding is resolved, (2) the sentinel
# survives untouched, (3) no new drift is introduced. Skips cleanly when claude is absent.
# Run directly, or via tests/run.sh with EVERGREEN_LLM_TESTS=1.
set -u
SCAN="$(cd "$(dirname "$0")/.." && pwd)/bin/evergreen-scan"
command -v claude >/dev/null 2>&1 || { echo "golden-prose: SKIP (no claude CLI)"; exit 0; }

PASS=0; FAIL=0
ok(){  PASS=$((PASS+1)); echo "PASS: $1"; }
bad(){ FAIL=$((FAIL+1)); echo "FAIL: $1"; }

t="$(mktemp -d)"
( cd "$t" || exit 1
  git init -q && git config user.email t@t && git config user.name t
  mkdir -p src docs
  printf 'real\n' > src/real.py
  printf '# Setup\n\nSENTINEL_UNRELATED_LINE must survive.\n\nRun `src/old_runner.py` to begin.\n' > docs/setup.md
  git add -A && git commit -qm init ) >/dev/null 2>&1

( cd "$t" && bash "$SCAN" 2>/dev/null ) | grep -q 'old_runner.py' \
  && ok "precondition: dead path is flagged before fix" || bad "precondition: dead path flagged"

( cd "$t" && bash "$SCAN" --fix-prose >/dev/null 2>&1 )
after="$(cd "$t" && bash "$SCAN" 2>/dev/null)"

printf '%s' "$after" | grep -q 'old_runner.py' \
  && bad "rubric: dead-path reference resolved (model left it / gate rejected)" \
  || ok "rubric: dead-path reference resolved"
grep -q 'SENTINEL_UNRELATED_LINE' "$t/docs/setup.md" \
  && ok "rubric: unrelated content preserved" || bad "rubric: unrelated content preserved"
printf '%s' "$after" | grep -q 'no drift detected' \
  && ok "rubric: no new drift introduced" || bad "rubric: no new drift introduced"
# no net line additions (a dead-path fix edits/removes, never injects a preamble)
nlines="$(wc -l < "$t/docs/setup.md")"
[ "$nlines" -le 5 ] \
  && ok "rubric: no net line additions (no injected preamble)" || bad "rubric: no net line additions (lines=$nlines, want <=5)"

rm -rf "$t"
echo "----------------------------------------"
echo "golden-prose: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
