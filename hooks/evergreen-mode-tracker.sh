#!/usr/bin/env bash
# Evergreen UserPromptSubmit — the SOLE writer of the mode state. Mirrors ponytail-mode-tracker.
# Detects a mode-change request in the prompt and persists off|light|strict to a per-repo flag.
# Accepts BOTH the raw slash form (/evergreen strict) and the expanded command form
# (set evergreen to strict) so it works however the host surfaces the command. No doc analysis.
set -u

EG_ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}"
MODE_FILE="$EG_ROOT/.evergreen-mode"

input="$(cat 2>/dev/null || true)"
p="$(printf '%s' "$input" | tr '[:upper:]' '[:lower:]')"

set_mode() {
  printf '%s' "$1" > "$MODE_FILE" 2>/dev/null && printf 'EVERGREEN MODE CHANGED — %s\n' "$1"
}

# Precedence: explicit strict > light > off > stop/normal > leave untouched.
if   printf '%s' "$p" | grep -qE '([/@$]?evergreen[: ]+strict\b|set evergreen to strict)'; then
  set_mode strict
elif printf '%s' "$p" | grep -qE '([/@$]?evergreen[: ]+light\b|set evergreen to light)'; then
  set_mode light
elif printf '%s' "$p" | grep -qE '([/@$]?evergreen[: ]+off\b|set evergreen to off)'; then
  set_mode off
elif printf '%s' "$p" | grep -qE '\b(stop evergreen|normal mode)\b'; then
  set_mode off
fi
exit 0
