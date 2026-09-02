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
# A mode change is ADDRESSED to evergreen, and the two form families are addressed differently.
# A sigil form must BEGIN A LINE, or else fall inside the opening window: the slash command
# expands to a body whose first line is the sigil, while a notification quoting one and the
# skill body naming the forms it documents carry them mid-sentence and far in. An imperative
# form is typed by hand, so it opens the prompt, and sixty characters covers the longest
# documented request plus a courtesy word. Past that window the same words are quotation, and
# both of those quotations rewrote this file before the rules existed.
opening="${p:0:60}"
# The optional `:mode` is the shipped slash command's own name (`/evergreen:mode strict`), whose
# expanded body opens with exactly that line. No other `:<command>` suffix is accepted, so
# `/evergreen:winnow off` asks winnow for something and never rewrites persistent state.
sigil_line(){
  printf '%s' "$p" | grep -qE "^[[:space:]]*[/@\$]evergreen(:mode)?[: ]+$1\b" \
    || printf '%s' "$opening" | grep -qE "[/@\$]evergreen(:mode)?[: ]+$1\b"
}
opening_has(){ printf '%s' "$opening" | grep -qE "$1"; }

set_mode() {
  if printf '%s' "$1" > "$MODE_FILE" 2>/dev/null; then
    exclude_mode_file
    printf 'EVERGREEN MODE CHANGED — %s\n' "$1"
  else
    printf 'evergreen: could not write %s\n' "$MODE_FILE" >&2
  fi
}

# The flag is per-repo state, not content: consumer repositories must never see it as untracked.
# info/exclude is the private half of .gitignore — never committed, so this edits nobody's tree.
exclude_mode_file() {
  local exclude
  exclude="$(git -C "$EG_ROOT" rev-parse --git-path info/exclude 2>/dev/null)" || return 0
  [ -n "$exclude" ] || return 0
  case "$exclude" in /*) ;; *) exclude="$EG_ROOT/$exclude" ;; esac
  mkdir -p "$(dirname "$exclude")" 2>/dev/null || return 0
  grep -qxF '.evergreen-mode' "$exclude" 2>/dev/null && return 0
  # An exclude file whose last line lacks its newline would otherwise swallow the new pattern.
  [ -s "$exclude" ] && [ -n "$(tail -c 1 "$exclude" 2>/dev/null)" ] && printf '\n' >> "$exclude"
  printf '.evergreen-mode\n' >> "$exclude" 2>/dev/null || true
}

# Never flip mode on a negated request ("don't stop evergreen", "do not set evergreen to strict").
if printf '%s' "$opening" | grep -qE "(don'?t|do not|never|won'?t|would ?n'?t|should ?n'?t)[^.!?]*evergreen|evergreen[^.!?]*(don'?t|do not|never)"; then
  exit 0
fi

# Precedence: strict > light > off. Terse forms REQUIRE a sigil (/evergreen strict, @evergreen: light)
# so descriptive prose that merely mentions a mode ("the evergreen strict mode is documented...")
# can never flip persistent state; imperative verb forms ("set evergreen to X", "stop evergreen") stay.
if   sigil_line strict || opening_has 'set evergreen to strict'; then
  set_mode strict
elif sigil_line light || opening_has 'set evergreen to light'; then
  set_mode light
elif sigil_line off || sigil_line normal \
     || opening_has 'set evergreen to off|stop evergreen|normal mode for evergreen'; then
  set_mode off
fi
exit 0
