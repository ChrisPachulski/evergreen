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

# Release identity must reach both Claude's injected digest/full skill and Codex's AGENTS rules.
for tok in "MARKETING_VERSION" "CURRENT_PROJECT_VERSION" "release_identity_drift" \
           "external build state unverified" "0.9.0 (72)" "0.9.1 (73)" \
           "upload-safe" "Universal Purchase" "upload, push"; do
  if grep -q "$tok" "$ROOT/skills/evergreen/SKILL.md" \
     && grep -q "$tok" "$ROOT/skills/evergreen/DIGEST.md" \
     && grep -q "$tok" "$ROOT/AGENTS.md"; then
    ok "release identity agrees across Claude/Codex: $tok"
  else
    no "release identity agrees across Claude/Codex: $tok"
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
printf '{"prompt":"The evergreen strict mode is documented in the README"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
[ ! -f "$TMP/.evergreen-mode" ] && ok "tracker: prose mentioning 'evergreen strict' does not flip state" || no "tracker: prose mention wrote $(cat "$TMP/.evergreen-mode")"
printf '{"prompt":"I read a post comparing evergreen: light docs approaches"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
[ ! -f "$TMP/.evergreen-mode" ] && ok "tracker: prose mentioning 'evergreen: light' does not flip state" || no "tracker: prose mention wrote $(cat "$TMP/.evergreen-mode")"
printf '{"prompt":"@evergreen: off"}' | bash "$HOOKS/evergreen-mode-tracker.sh" >/dev/null
eq "$(cat "$TMP/.evergreen-mode" 2>/dev/null)" "off" "tracker: sigiled @evergreen: off -> off"
rm -f "$TMP/.evergreen-mode"

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
PYTHON3="$(command -v python3)"
guard_payload(){ "$PYTHON3" -c 'import json, sys; print(json.dumps({"tool_input": {"command": sys.argv[1]}}))' "$1"; }
guard_input(){ printf '%s' "$1" | bash "$HOOKS/evergreen-guard.sh" 2>&1; }
guard_cmd(){ guard_input "$(guard_payload "$1")"; }
guard(){ guard_cmd "git commit -m x"; }
guard_rc(){ payload="$(guard_payload "$1")"; printf '%s' "$payload" | bash "$HOOKS/evergreen-guard.sh" >/dev/null 2>&1; printf '%s' "$?"; }
printf 'k\n' > "$TMP/.env"; git -C "$TMP" add -f .env
has "$(guard)" "secret/credential" "guard: staged .env -> blocked"
has "$(guard_cmd "git -C $TMP commit -m x")" "secret/credential" "guard: git -C commit inspects staged index"
has "$(guard_cmd "git -C \"$TMP\" commit -m x")" "secret/credential" "guard: git -C quoted path inspects staged index"
has "$(guard_cmd "git -c color.ui=false commit -m x")" "secret/credential" "guard: git -c commit inspects staged index"
has "$(guard_cmd "git --no-pager commit -m x")" "secret/credential" "guard: ordinary global option commit inspects staged index"
printf '.env\n' > "$TMP/.evergreen-keep"
empty "$(guard_cmd "git -C $TMP commit -m x")" "guard: .evergreen-keep suppresses the block"
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
has "$(guard_cmd "echo git add && echo git commit")" "separate" \
  "guard: conservative policy blocks recognizable Git intents in one call"
git -C "$TMP" rm -q --cached .env; rm -f "$TMP/.env"

# Staging and committing in one Bash call must be split: the PreToolUse hook cannot inspect the
# index after the add but before the commit. This applies through supported Git global options.
printf 'k\n' > "$TMP/.env"
for case_row in \
  "git add -f .env && git commit -m x|ampersand" \
  "git add -f .env; git commit -m x|semicolon" \
  "git -C $TMP add -f .env && git -C $TMP commit -m x|git -C" \
  "git -C \"$TMP\" add -f .env && git -C \"$TMP\" commit -m x|quoted git -C" \
  "git -c color.ui=false add -f .env && git -c color.ui=false commit -m x|git -c" \
  "git --no-pager add -f .env && git --no-pager commit -m x|global option" \
  "git add . && (git commit -m x)|parenthesized commit" \
  "command git add . && command git commit -m x|command wrapper" \
  "git add . && env X=1 git commit -m x|env wrapper" \
  "sh -c 'git add . && git commit -m x'|sh -c wrapper" \
  "git -C '/tmp/my repo' add . && git -C '/tmp/my repo' commit -m x|single-quoted git -C" \
  "git -C \"/tmp/my repo\" add . && git -C \"/tmp/my repo\" commit -m x|double-quoted git -C" \
  "git -C /tmp/my\\ repo add . && git -C /tmp/my\\ repo commit -m x|escaped git -C" \
  "git add . && { git commit -m x; }|brace group" \
  "stage(){ git add .; }; stage && git commit -m x|shell function" \
  "git add . && if true; then git commit -m x; fi|conditional" \
  "eval 'git add .' && git commit -m x|eval wrapper" \
  "git add . & wait; git commit -m x|background and wait"; do
  cmd="${case_row%|*}"
  label="${case_row##*|}"
  has "$(guard_cmd "$cmd")" "separate" "guard: compound $label stage-and-commit is blocked"
  eq "$(guard_rc "$cmd")" "2" "guard: compound $label exits 2"
done
spaced_json='{ "tool_input": { "command": "git add . && git commit -m x" } }'
has "$(guard_input "$spaced_json")" "separate" "guard: JSON whitespace serialization is classified"
eq "$(printf '%s' "$spaced_json" | bash "$HOOKS/evergreen-guard.sh" >/dev/null 2>&1; printf '%s' "$?")" "2" \
  "guard: JSON whitespace serialization exits 2"
for case_row in \
  "git commit -m x && git add .|reverse commit-then-add" \
  "git add . || git commit -m x|mutually exclusive add-or-commit"; do
  cmd="${case_row%|*}"
  label="${case_row##*|}"
  has "$(guard_cmd "$cmd")" "separate" "guard: conservative policy blocks $label"
  has "$(guard_cmd "$cmd")" "EVERGREEN_GUARD=off" "guard: $label rejection names bypass"
  eq "$(guard_rc "$cmd")" "2" "guard: conservative $label exits 2"
done
empty "$(EVERGREEN_GUARD=off guard_cmd "git add -f .env && git commit -m x")" \
  "guard: EVERGREEN_GUARD=off bypasses compound rejection"

mkdir -p "$TMP/no-python"
printf '#!/bin/sh\nexit 127\n' > "$TMP/no-python/python3"
chmod +x "$TMP/no-python/python3"
plain_payload="$(guard_payload "ls -la")"
empty "$(printf '%s' "$plain_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "guard: unavailable Python does not block unrelated commands"
compound_payload="$(guard_payload "git add . && git commit -m x")"
has "$(printf '%s' "$compound_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "separate" "guard: unavailable Python still blocks obvious compound Git"
eq "$(printf '%s' "$compound_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" >/dev/null 2>&1; printf '%s' "$?")" \
  "2" "guard: unavailable Python compound Git exits 2"
git -C "$TMP" add -f .env
commit_payload="$(guard_payload "git commit -m x")"
has "$(printf '%s' "$commit_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "secret/credential" "guard: unavailable Python commit still inspects staged index"
git -C "$TMP" rm -q --cached .env
rm -rf "$TMP/no-python"
rm -f "$TMP/.env"

# A commit that DELETES slop is the guard doing its job — must never be blocked (--diff-filter=d).
printf 's\n' > "$TMP/AUDIT-2026-01-01.md"; git -C "$TMP" add -f AUDIT-2026-01-01.md
git -C "$TMP" commit -qm "add slop to be removed"
git -C "$TMP" rm -q AUDIT-2026-01-01.md   # stage a pure deletion of the slop file
empty "$(guard_cmd "git -C $TMP commit -m cleanup")" \
  "guard: deletion-only option-prefixed commit of slop -> allowed (cleanup enforced, not blocked)"
git -C "$TMP" commit -qm "remove slop"

# --- companion python selftests (comment renderer + benchmark scorer) ---
python3 "$ROOT/ci/pr_comment.py" --selftest >/dev/null 2>&1 && ok "ci/pr_comment.py --selftest" || no "ci/pr_comment.py --selftest"
python3 "$ROOT/eval/bench/run_bench.py" --selftest >/dev/null 2>&1 && ok "eval/bench/run_bench.py --selftest" || no "eval/bench/run_bench.py --selftest"

# --- registration ---
hooks_json="$(cat "$ROOT/hooks/hooks.json")"
has "$hooks_json" "SessionStart" "hooks.json registers SessionStart"
has "$hooks_json" "UserPromptSubmit" "hooks.json registers UserPromptSubmit"
has "$hooks_json" "Stop" "hooks.json registers Stop"

echo
if [ "$fails" -eq 0 ]; then echo "all passed"; exit 0; else echo "$fails failed"; exit 1; fi
