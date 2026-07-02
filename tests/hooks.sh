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
hasnt "$out" "name: evergreen" "activate: YAML frontmatter stripped from injection"
has "$out" "session digest" "activate: injects the digest, not the full skill"
hasnt "$out" "## Taxonomy" "activate: full SKILL body not injected (negative assertion)"
# Digest must not drift from the skill it condenses — every load-bearing token in both.
for tok in "Prove it or drop it" "Vanished path" "Dead contract" "left alone:" \
           "docs still match" "Age is not drift" "behavior-asserted"; do
  if grep -q "$tok" "$ROOT/skills/evergreen/DIGEST.md" && grep -q "$tok" "$ROOT/skills/evergreen/SKILL.md"; then
    ok "digest/skill agree on: $tok"
  else
    no "digest/skill agree on: $tok"
  fi
done
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
rm -f "$TMP/.evergreen-mode"
printf '{"prompt":"is that the normal mode for this feature?"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
[ ! -f "$TMP/.evergreen-mode" ] && ok "tracker: bare 'normal mode' does not flip state" || no "tracker: bare 'normal mode' wrote $(cat "$TMP/.evergreen-mode")"
printf '{"prompt":"do not stop evergreen"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
[ ! -f "$TMP/.evergreen-mode" ] && ok "tracker: negated request does not flip state" || no "tracker: negation wrote $(cat "$TMP/.evergreen-mode")"
printf '{"prompt":"hi","context":"set evergreen to strict"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
[ ! -f "$TMP/.evergreen-mode" ] && ok "tracker: non-prompt JSON field does not flip state" || no "tracker: non-prompt field wrote $(cat "$TMP/.evergreen-mode")"

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

# --- stop hook dedup (fire once per change state, not every turn) ---
printf 'w\n' >> "$TMP/app.py"
has "$(bash "$HOOKS/evergreen-stop.sh")" "freshness pass" "stop: fresh code change -> fires"
empty "$(bash "$HOOKS/evergreen-stop.sh")" "stop: same dirty tree next turn -> silent (dedup)"
printf 'w2\n' >> "$TMP/app.py"
has "$(bash "$HOOKS/evergreen-stop.sh")" "freshness pass" "stop: further edit -> fires again"
git -C "$TMP" checkout -q -- app.py

# --- guard (PreToolUse commit backstop) ---
guard(){ printf '{"tool_input":{"command":"git commit -m x"}}' | bash "$HOOKS/evergreen-guard.sh" 2>&1; }
printf 'k\n' > "$TMP/.env"; git -C "$TMP" add -f .env
has "$(guard)" "secret/credential" "guard: staged .env -> blocked"
printf '.env\n' > "$TMP/.evergreen-keep"
empty "$(guard)" "guard: .evergreen-keep suppresses the block"
rm -f "$TMP/.evergreen-keep"
empty "$(EVERGREEN_GUARD=off guard)" "guard: EVERGREEN_GUARD=off bypasses"
git -C "$TMP" rm -q --cached .env; rm -f "$TMP/.env"
printf 's\n' > "$TMP/SESSION_SUMMARY.md"; git -C "$TMP" add SESSION_SUMMARY.md
has "$(guard)" "AI-slop report" "guard: staged SESSION_SUMMARY.md -> blocked"
git -C "$TMP" rm -q --cached SESSION_SUMMARY.md; rm -f "$TMP/SESSION_SUMMARY.md"
mkdir -p "$TMP/src"; printf '# toc\n' > "$TMP/src/SUMMARY.md"; git -C "$TMP" add src/SUMMARY.md
empty "$(guard)" "guard: mdBook src/SUMMARY.md -> allowed"
git -C "$TMP" rm -q --cached src/SUMMARY.md; rm -rf "$TMP/src"
printf '# toc\n' > "$TMP/SUMMARY.md"; git -C "$TMP" add SUMMARY.md
empty "$(guard)" "guard: GitBook root SUMMARY.md -> allowed"
git -C "$TMP" rm -q --cached SUMMARY.md; rm -f "$TMP/SUMMARY.md"
printf 'k\n' > "$TMP/.env"; git -C "$TMP" add -f .env
empty "$(printf '{"tool_input":{"command":"ls -la"}}' | bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "guard: non-git command -> pass-through despite staged secret"
git -C "$TMP" rm -q --cached .env; rm -f "$TMP/.env"

# --- registration ---
hooks_json="$(cat "$ROOT/hooks/hooks.json")"
has "$hooks_json" "SessionStart" "hooks.json registers SessionStart"
has "$hooks_json" "UserPromptSubmit" "hooks.json registers UserPromptSubmit"
has "$hooks_json" "Stop" "hooks.json registers Stop"

echo
if [ "$fails" -eq 0 ]; then echo "all passed"; exit 0; else echo "$fails failed"; exit 1; fi
