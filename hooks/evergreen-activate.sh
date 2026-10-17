#!/usr/bin/env bash
# Evergreen SessionStart activation.
# Reads the persisted per-repo mode and injects the (mode-filtered) freshness reflex as
# session context (stdout becomes SessionStart context in Claude Code). PURE INJECTION:
# this hook never reads or analyzes documentation content. Exits 0 always.
set -u

EG_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
[ "${EG_ROOT#/}" = "$EG_ROOT" ] && EG_ROOT="$PWD"   # ensure absolute
MODE_FILE="$EG_ROOT/.evergreen-mode"

MODE="light"   # default when no state file
if [ -r "$MODE_FILE" ]; then
  m="$(tr -d '[:space:]' < "$MODE_FILE" 2>/dev/null || true)"
  case "$m" in off|light|strict) MODE="$m" ;; esac
fi

# off — inject nothing operative.
[ "$MODE" = "off" ] && exit 0

PREAMBLE=""
case "$MODE" in
  light)  PREAMBLE="EVERGREEN MODE: light — run ladder rungs 1-3 (vanished paths, dead contracts, drifted snippets) only; never emit a prose finding. Defer the deep semantic rung-4 read unless asked." ;;
  strict) PREAMBLE="EVERGREEN MODE: strict — run all four rungs, including the full rung-4 semantic prose pass." ;;
esac

printf 'EVERGREEN REFLEX ACTIVE — mode: %s\n\n%s\n\n' "$MODE" "$PREAMBLE"

# Emit the operative ruleset. The DIGEST (about two-fifths of the full skill by words) is enough
# for the ride-along reflex; the full SKILL.md stays loadable on demand via the Skill tool. Fall
# back to the frontmatter-stripped SKILL body if the digest is missing, and say so: the fallback
# injects roughly two and a half times the tokens the session budgeted for, which is a fact the
# reader deserves rather than a silent overrun. Best-effort.
BASE="${CLAUDE_PLUGIN_ROOT:-$EG_ROOT}/skills/evergreen"
if [ -r "$BASE/DIGEST.md" ]; then
  cat "$BASE/DIGEST.md"
elif [ -r "$BASE/SKILL.md" ]; then
  printf 'EVERGREEN: digest missing, injecting full skill\n'
  awk 'NR==1 && /^---[[:space:]]*$/ {f=1; next} f && /^---[[:space:]]*$/ {f=0; next} !f' "$BASE/SKILL.md"
fi
exit 0
