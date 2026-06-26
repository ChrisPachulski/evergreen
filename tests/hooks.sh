#!/usr/bin/env bash
# One self-contained check for evergreen's hooks (the only deterministic part of the skill).
# No framework. Run: bash tests/hooks.sh
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HOOKS="$ROOT/hooks"
export CLAUDE_PLUGIN_ROOT="$ROOT"

fails=0
ok(){ printf 'ok   - %s\n' "$1"; }
no(){ printf 'FAIL - %s\n' "$1"; fails=$((fails+1)); }
has(){ printf '%s' "$1" | grep -q "$2" && ok "$3" || no "$3"; }
hasnt(){ printf '%s' "$1" | grep -q "$2" && no "$3" || ok "$3"; }
empty(){ [ -z "$1" ] && ok "$2" || no "$2 (got: $1)"; }
eq(){ [ "$1" = "$2" ] && ok "$3" || no "$3 (got '$1' want '$2')"; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git -C "$TMP" init -q
git -C "$TMP" config user.email t@t.t; git -C "$TMP" config user.name t
printf '# doc\n' > "$TMP/README.md"
printf 'x\n' > "$TMP/app.py"
printf '.evergreen-mode\n' > "$TMP/.gitignore"
git -C "$TMP" add -A; git -C "$TMP" commit -qm init >/dev/null
export CLAUDE_PROJECT_DIR="$TMP"

# --- activate (SessionStart injection, mode-filtered) ---
rm -f "$TMP/.evergreen-mode"
out="$(bash "$HOOKS/evergreen-activate.sh")"
has "$out" "EVERGREEN REFLEX ACTIVE" "activate: default light injects the ruleset"
has "$out" "rungs 1-3" "activate: light preamble present"
hasnt "$out" "all four rungs" "activate: light defers full rung-4 (negative assertion)"
printf 'strict' > "$TMP/.evergreen-mode"
out="$(bash "$HOOKS/evergreen-activate.sh")"
has "$out" "all four rungs" "activate: strict includes the full semantic pass"
printf 'off' > "$TMP/.evergreen-mode"
out="$(bash "$HOOKS/evergreen-activate.sh")"
empty "$out" "activate: off injects nothing operative"

# --- mode tracker (sole writer; grammar + precedence) ---
rm -f "$TMP/.evergreen-mode"
printf '{"prompt":"/evergreen strict"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
eq "$(cat "$TMP/.evergreen-mode")" "strict" "tracker: /evergreen strict -> strict"
printf '{"prompt":"please set evergreen to light"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
eq "$(cat "$TMP/.evergreen-mode")" "light" "tracker: set evergreen to light -> light"
printf '{"prompt":"stop evergreen"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
eq "$(cat "$TMP/.evergreen-mode")" "off" "tracker: stop evergreen -> off"
printf '{"prompt":"just a normal message"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
eq "$(cat "$TMP/.evergreen-mode")" "off" "tracker: unrelated prompt leaves mode untouched"

# --- stop hook (post-turn audit request; guards + mode gate) ---
rm -f "$TMP/.evergreen-mode"
empty "$(bash "$HOOKS/evergreen-stop.sh")" "stop: clean tree -> silent"
printf 'y\n' >> "$TMP/app.py"
has "$(bash "$HOOKS/evergreen-stop.sh")" "freshness pass" "stop: unstaged code change -> fires"
git -C "$TMP" add app.py
has "$(bash "$HOOKS/evergreen-stop.sh")" "freshness pass" "stop: staged code change -> fires"
git -C "$TMP" reset -q >/dev/null; git -C "$TMP" checkout -q -- app.py
printf 'z\n' > "$TMP/new.py"
has "$(bash "$HOOKS/evergreen-stop.sh")" "freshness pass" "stop: untracked source -> fires"
rm -f "$TMP/new.py"
printf 'more\n' >> "$TMP/README.md"
empty "$(bash "$HOOKS/evergreen-stop.sh")" "stop: doc-only change -> silent"
git -C "$TMP" checkout -q -- README.md
printf 'q\n' >> "$TMP/app.py"
printf 'off' > "$TMP/.evergreen-mode"
empty "$(bash "$HOOKS/evergreen-stop.sh")" "stop: off -> silent despite code change"
git -C "$TMP" checkout -q -- app.py; rm -f "$TMP/.evergreen-mode"

# --- registration ---
hooks_json="$(cat "$ROOT/hooks/hooks.json")"
has "$hooks_json" "SessionStart" "hooks.json registers SessionStart"
has "$hooks_json" "UserPromptSubmit" "hooks.json registers UserPromptSubmit"

echo
if [ "$fails" -eq 0 ]; then echo "all passed"; exit 0; else echo "$fails failed"; exit 1; fi
