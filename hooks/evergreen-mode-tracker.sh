#!/usr/bin/env bash
# Evergreen UserPromptSubmit — the SOLE writer of the mode state.
# Detects a mode-change request in the prompt and persists off|light|strict to a per-repo flag.
# Only fires when "evergreen" is named, ignores negated requests, never changes state on a bare
# phrase, and matches against the prompt field (not the whole JSON payload). No doc analysis.
set -u

EG_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
[ "${EG_ROOT#/}" = "$EG_ROOT" ] && EG_ROOT="$PWD"   # ensure absolute
MODE_FILE="$EG_ROOT/.evergreen-mode"

input="$(cat 2>/dev/null || true)"
# Match the prompt field only; fall back to the raw input if JSON parsing is unavailable.
p="$(printf '%s' "$input" | python3 -c 'import sys,json
try: print(json.load(sys.stdin).get("prompt",""))
except Exception: pass' 2>/dev/null)"
[ -z "$p" ] && p="$input"
p="$(printf '%s' "$p" | tr '[:upper:]' '[:lower:]')"

set_mode() {
  if printf '%s' "$1" > "$MODE_FILE" 2>/dev/null; then
    printf 'EVERGREEN MODE CHANGED — %s\n' "$1"
  else
    printf 'evergreen: could not write %s\n' "$MODE_FILE" >&2
  fi
}

# Never flip mode on a negated request ("don't stop evergreen", "do not set evergreen to strict").
if printf '%s' "$p" | grep -qE "(don'?t|do not|never|won'?t|would ?n'?t|should ?n'?t)[^.!?]*evergreen|evergreen[^.!?]*(don'?t|do not|never)"; then
  exit 0
fi

# Precedence: strict > light > off. Terse forms REQUIRE a sigil (/evergreen strict, @evergreen: light)
# so descriptive prose that merely mentions a mode ("the evergreen strict mode is documented...")
# can never flip persistent state; imperative verb forms ("set evergreen to X", "stop evergreen") stay.
if   printf '%s' "$p" | grep -qE '([/@$]evergreen[: ]+strict\b|set evergreen to strict)'; then
  set_mode strict
elif printf '%s' "$p" | grep -qE '([/@$]evergreen[: ]+light\b|set evergreen to light)'; then
  set_mode light
elif printf '%s' "$p" | grep -qE '([/@$]evergreen[: ]+off\b|set evergreen to off|stop evergreen|[/@$]evergreen[: ]+normal\b|normal mode for evergreen)'; then
  set_mode off
fi
exit 0
