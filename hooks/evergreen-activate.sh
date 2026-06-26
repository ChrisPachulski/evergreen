#!/usr/bin/env bash
# Evergreen SessionStart activation — mirrors ponytail-activate.
# Reads the persisted per-repo mode and injects the (mode-filtered) freshness reflex as
# session context (stdout becomes SessionStart context in Claude Code). PURE INJECTION:
# this hook never reads or analyzes documentation content. Exits 0 always.
set -u

EG_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
MODE_FILE="$EG_ROOT/.evergreen-mode"

MODE="light"   # default when no state file
if [ -r "$MODE_FILE" ]; then
  m="$(tr -d '[:space:]' < "$MODE_FILE" 2>/dev/null || true)"
  case "$m" in off|light|strict) MODE="$m" ;; esac
fi

# off — inject nothing operative (parity with ponytail skipping activation when off).
[ "$MODE" = "off" ] && exit 0

case "$MODE" in
  light)  PREAMBLE="EVERGREEN MODE: light — run ladder rungs 1-3 (vanished paths, dead contracts, drifted snippets) plus cite-only prose checks. Defer the deep semantic rung-4 read unless asked." ;;
  strict) PREAMBLE="EVERGREEN MODE: strict — run all four rungs, including the full rung-4 semantic prose pass." ;;
esac

printf 'EVERGREEN REFLEX ACTIVE — mode: %s\n\n%s\n\n' "$MODE" "$PREAMBLE"

# Emit the operative ruleset = the SKILL body with YAML frontmatter stripped. Best-effort.
SKILL="${CLAUDE_PLUGIN_ROOT:-$EG_ROOT}/skills/evergreen/SKILL.md"
if [ -r "$SKILL" ]; then
  awk 'NR==1 && /^---[[:space:]]*$/ {f=1; next} f && /^---[[:space:]]*$/ {f=0; next} !f' "$SKILL"
fi
exit 0
