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

if ROOT="$ROOT" python3 - <<'PY'
import os
from pathlib import Path
import re

root = Path(os.environ["ROOT"])
digest = re.findall(r"\b[\w'-]+\b", (root / "skills/evergreen/DIGEST.md").read_text())
skill = re.findall(r"\b[\w'-]+\b", (root / "skills/evergreen/SKILL.md").read_text())
ratio = len(digest) / len(skill)
readme = (root / "README.md").read_text()
assert 0.30 <= ratio <= 0.40, ratio
assert "compact digest—currently about one-third of the full skill by words" in readme
assert "~40-line" not in readme
PY
then
  ok "README describes the measured digest/skill relationship without a line-count claim"
else
  no "README describes the measured digest/skill relationship without a line-count claim"
fi

# Passive providers and source maps may expand review scope, never decide the review.
for tok in \
  "Provider evidence and source maps nominate candidates, never findings or verdicts." \
  "Re-read every candidate against current code before deciding drift."; do
  if grep -Fq "$tok" "$ROOT/README.md" \
     && grep -Fq "$tok" "$ROOT/docs/DESIGN.md" \
     && grep -Fq "$tok" "$ROOT/skills/evergreen/SKILL.md" \
     && grep -Fq "$tok" "$ROOT/skills/evergreen/DIGEST.md" \
     && grep -Fq "$tok" "$ROOT/AGENTS.md"; then
    ok "provider candidate boundary agrees across product/Claude/Codex: $tok"
  else
    no "provider candidate boundary agrees across product/Claude/Codex: $tok"
  fi
done

for file in "$ROOT/README.md" "$ROOT/docs/DESIGN.md" "$ROOT/skills/evergreen/SKILL.md"; do
  grep -Fq "Drift-shaped" "$file" \
    && ok "Drift-shaped interoperability documented in ${file#"$ROOT/"}" \
    || no "Drift-shaped interoperability documented in ${file#"$ROOT/"}"
done

if ROOT="$ROOT" python3 - <<'PY'
import json
import os
from pathlib import Path
import sys

root = Path(os.environ["ROOT"])
sys.path.insert(0, str(root))
from evergreen.evidence import load_evidence

path = root / "examples/provider-evidence.json"
records = json.loads(path.read_text())
assert len(records) == 2
assert {record["type"] for record in records} == {
    "constant-value-changed", "return-contract-changed",
}
assert all(record["confidence"] == "deterministic" for record in records)
assert all(not ({"finding", "verdict", "drift", "status"} & set(record)) for record in records)
assert next(record for record in records if record["type"] == "return-contract-changed")["line"] == 11
loaded, warnings = load_evidence(path, root)
assert len(loaded) == 2 and warnings == []

fixture = (root / "examples/provider-boundary.md").read_text()
assert "Expected: finding" in fixture
assert "Expected: no finding" in fixture
assert "per-project timeout override remains true" in fixture
PY
then
  ok "provider evidence and semantic false-positive fixtures"
else
  no "provider evidence and semantic false-positive fixtures"
fi

# The shipped docs and both host instruction surfaces must describe one CI trust contract.
for tok in "deterministic trust layer" "complete with findings" "complete with unverified" \
           "fail_on_inconclusive" "untrusted data" "separate tool calls"; do
  if grep -Fq "$tok" "$ROOT/README.md" \
     && grep -Fq "$tok" "$ROOT/docs/DESIGN.md" \
     && grep -Fq "$tok" "$ROOT/skills/evergreen/SKILL.md" \
     && grep -Fq "$tok" "$ROOT/skills/evergreen/DIGEST.md" \
     && grep -Fq "$tok" "$ROOT/AGENTS.md"; then
    ok "trust semantics agree across product/Claude/Codex: $tok"
  else
    no "trust semantics agree across product/Claude/Codex: $tok"
  fi
done

grep -Fq "Under the default fail-closed policy, a green check means" "$ROOT/README.md" \
  && ok "README qualifies green-check meaning by the default fail-closed policy" \
  || no "README qualifies green-check meaning by the default fail-closed policy"

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

# Package, registry, CLI, and deployed-doc release claims share one cross-host contract.
for tok in \
  "Release identity spans package manifests, registry versions, and version-reporting CLI output." \
  "Audit version-bearing badges, version-reporting installed-command examples, generated API version labels or headers, and deployed docs version labels as linked release claims." \
  "Interpret each claim's meaning: current source and latest published release may legitimately differ." \
  "Keep independently versioned packages and platforms as independent release streams unless repository policy explicitly couples them." \
  "Without direct registry, store, or deployment evidence, report external release state unverified." \
  "Never publish, upload, push, deploy, or mutate a portal or registry without explicit user authority."; do
  if grep -Fq "$tok" "$ROOT/README.md" \
     && grep -Fq "$tok" "$ROOT/docs/DESIGN.md" \
     && grep -Fq "$tok" "$ROOT/skills/evergreen/SKILL.md" \
     && grep -Fq "$tok" "$ROOT/skills/evergreen/DIGEST.md" \
     && grep -Fq "$tok" "$ROOT/AGENTS.md"; then
    ok "general release identity agrees across product/Claude/Codex: $tok"
  else
    no "general release identity agrees across product/Claude/Codex: $tok"
  fi
done

if ROOT="$ROOT" python3 - <<'PY'
import os
import json
import re
from pathlib import Path

root = Path(os.environ["ROOT"])
fixture = (root / "examples/package-release-identity.md").read_text()

def fenced(heading, language):
    marker = f"### {heading}\n"
    assert marker in fixture, f"missing section {heading}"
    section = fixture.split(marker, 1)[1].split("\n### ", 1)[0]
    match = re.search(rf"```{language}\n(.*?)\n```", section, re.DOTALL)
    assert match, f"missing {language} example for {heading}"
    return match.group(1).strip()

manifest_version = json.loads(fenced("`package.json` — current source", "json"))["version"]
cli_source = fenced("CLI version source — current source", "javascript")
cli_output = fenced("CLI version output — current source", "text")
assert "package.json" in cli_source and cli_output == manifest_version == "1.4.0"

def only_version(text):
    versions = re.findall(r"\b\d+\.\d+\.\d+\b", text)
    assert len(versions) == 1, versions
    return versions[0]

published_claims = {
    "registry badge": fenced("Registry badge — latest published release", "markdown"),
    "installed command": fenced("Installed-command example — latest published release", "console"),
    "deployed docs": fenced("Deployed docs label — latest published release", "text"),
}
assert {only_version(value) for value in published_claims.values()} == {"1.3.2"}
api_header = fenced("Generated API header — current source", "html")
assert only_version(api_header) == "1.3.2" != manifest_version

assert "registry badge may be correct while `1.4.0` remains unreleased" in fixture
assert "Expected: release_identity_drift — the generated API header" in fixture
assert "Expected: external release state unverified" in fixture
assert "independent release stream" in fixture
PY
then
  ok "package release example parses ownership-aware version claims and compares them"
else
  no "package release example parses ownership-aware version claims and compares them"
fi

# Final product docs and release identity must agree across every shipped host surface.
for tok in \
  "Evidence providers and source maps are passive candidate inputs; Evergreen never executes provider commands or accepts their verdicts." \
  "Executable proof is local and explicit; CI never executes pull-request code, and unsafe or unavailable isolation is inconclusive." \
  "Current five-language benchmark metrics remain unpublished until one compatible run clears every declared coverage gate." \
  "Evergreen is not a hosted index, AST engine, dashboard, or automatic truth-path prose rewriter."; do
  if grep -Fq "$tok" "$ROOT/README.md" && grep -Fq "$tok" "$ROOT/docs/DESIGN.md"; then
    ok "final product boundary agrees across README/design: $tok"
  else
    no "final product boundary agrees across README/design: $tok"
  fi
done

for tok in \
  "./bin/evergreen impact --repo ." \
  "./bin/evergreen install --host claude" \
  "./bin/evergreen install --host codex" \
  "./bin/evergreen doctor --host all --repo ." \
  "./bin/evergreen uninstall --host all"; do
  grep -Fq "$tok" "$ROOT/README.md" \
    && ok "README documents shipped local/host command: $tok" \
    || no "README documents shipped local/host command: $tok"
done

if ROOT="$ROOT" python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["ROOT"])
claude = json.loads((root / ".claude-plugin/plugin.json").read_text())
codex = json.loads((root / ".codex-plugin/plugin.json").read_text())
marketplace = json.loads((root / ".claude-plugin/marketplace.json").read_text())
entry = next(item for item in marketplace["plugins"] if item["name"] == "evergreen")

assert {claude["version"], codex["version"], entry["version"]} == {"0.4.0"}
assert all("build" not in item and "build_number" not in item for item in (claude, codex, entry))
assert "0.4.0" in marketplace["description"]
for description in (
    claude["description"], codex["description"], marketplace["description"], entry["description"],
):
    lowered = description.lower()
    assert "impact" in lowered and "install" in lowered and "release identity" in lowered
PY
then
  ok "release 0.4.0 agrees across Claude/Codex/marketplace without a build counter"
else
  no "release 0.4.0 agrees across Claude/Codex/marketplace without a build counter"
fi

# Execute both passive-input examples and both semantic branches they nominate.
if ROOT="$ROOT" python3 - <<'PY'
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile

root = Path(os.environ["ROOT"])
command = root / "bin/evergreen"

provided = subprocess.run(
    [sys.executable, str(command), "impact", "--json", "--repo", str(root),
     "--evidence", str(root / "examples/provider-evidence.json"),
     "eval/fixture/config.py"],
    check=True, capture_output=True, text=True,
)
provider_payload = json.loads(provided.stdout)
assert provider_payload["warnings"] == []
assert [item["path"] for item in provider_payload["candidates"]] == [
    "eval/fixture/README.md", "examples/provider-boundary.md", "README.md",
    "eval/fixture/config.py",
]
config_candidate = provider_payload["candidates"][-1]
assert len(config_candidate["reasons"]) == 3
assert any("changed path" in reason for reason in config_candidate["reasons"])

with tempfile.TemporaryDirectory() as temporary:
    repo = Path(temporary)
    (repo / "src/public-api").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "src/public-api/client.py").write_text("value = 1\n")
    (repo / "docs/api.md").write_text("# API\n")
    (repo / "README.md").write_text("# Example\n")
    (repo / ".evergreen-map.json").write_text(
        (root / "examples/evergreen-map.json").read_text()
    )
    mapped = subprocess.run(
        [sys.executable, str(command), "impact", "--json", "--repo", str(repo),
         "src/public-api/client.py"],
        check=True, capture_output=True, text=True,
    )
    map_payload = json.loads(mapped.stdout)
    assert map_payload["warnings"] == []
    assert [item["path"] for item in map_payload["candidates"][:2]] == [
        "docs/api.md", "README.md",
    ]

load_config = runpy.run_path(str(root / "eval/fixture/config.py"))["load_config"]
with tempfile.TemporaryDirectory() as temporary:
    missing = Path(temporary) / "missing.json"
    missing.write_text("{}")
    try:
        load_config(missing)
    except KeyError:
        pass
    else:
        raise AssertionError("missing project must raise KeyError")

    override = Path(temporary) / "override.json"
    override.write_text(json.dumps({"project": "demo", "timeout": 45}))
    assert load_config(override)["timeout"] == 45
PY
then
  ok "provider evidence, source map, mismatch, and false-positive branches execute directly"
else
  no "provider evidence, source map, mismatch, and false-positive branches execute directly"
fi

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
  "git add . & wait; git commit -m x|background and wait" \
  "g''it add . && git commit -m x|quoted git fragment" \
  "git a''dd . && git commit -m x|quoted add fragment" \
  "git add . && git com''mit -m x|quoted commit fragment" \
  "g\\it a\\dd . && g\\it com\\mit -m x|backslash-split words"; do
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
unsafe_payload="$(guard_payload "git commit -am x")"
has "$(printf '%s' "$unsafe_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "separately staged plain commit" "guard: unavailable Python blocks unsafe commit modes"
eq "$(printf '%s' "$unsafe_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" >/dev/null 2>&1; printf '%s' "$?")" \
  "2" "guard: unavailable Python unsafe commit mode exits 2"
joined_payload="$(guard_payload "g''it a''dd . && g''it com''mit -m x")"
has "$(printf '%s' "$joined_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "separate" "guard: unavailable Python joins quoted Git fragments"
escaped_payload="$(guard_payload "g\\it a\\dd . && g\\it com\\mit -m x")"
has "$(printf '%s' "$escaped_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "separate" "guard: unavailable Python joins backslash-split Git words"
continued_git_payload="$(guard_payload $'g\\\nit add . && git commit -m x')"
has "$(printf '%s' "$continued_git_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "separate" "guard: unavailable Python joins continued git word"
continued_add_payload="$(guard_payload $'git a\\\ndd . && git commit -m x')"
has "$(printf '%s' "$continued_add_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "separate" "guard: unavailable Python joins continued add word"
continued_commit_payload="$(guard_payload $'git add . && git com\\\nmit -m x')"
has "$(printf '%s' "$continued_commit_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "separate" "guard: unavailable Python joins continued commit word"
git -C "$TMP" add -f .env
commit_payload="$(guard_payload "git commit -m x")"
has "$(printf '%s' "$commit_payload" | PATH="$TMP/no-python:$PATH" bash "$HOOKS/evergreen-guard.sh" 2>&1)" \
  "secret/credential" "guard: unavailable Python commit still inspects staged index"
git -C "$TMP" rm -q --cached .env
rm -rf "$TMP/no-python"
rm -f "$TMP/.env"

# Commit modes that can pull tracked working-tree content around the inspected index must be
# rejected. A plain commit still inspects only the separately finalized staged index.
printf 'baseline\n' > "$TMP/.env"; git -C "$TMP" add -f .env
git -C "$TMP" commit -qm "track env fixture"
printf 'unstaged secret\n' > "$TMP/.env"
for case_row in \
  "git commit -am x|-am" \
  "git commit -qam x|combined -qam" \
  "git commit --all -m x|--all" \
  "git commit --include .env -m x|--include" \
  "git commit --only .env -m x|--only" \
  "git commit -i .env -m x|short -i" \
  "git commit -o .env -m x|short -o" \
  "git commit -p -m x|short -p" \
  "git commit -m x .env|positional pathspec" \
  "git commit -m x -- .env|double-dash pathspec" \
  "git commit --pathspec-from-file=paths -m x|pathspec file" \
  "git -C $TMP commit -am x|git -C wrapper" \
  "command git commit --all -m x|command wrapper" \
  "env X=1 git commit --include .env -m x|env wrapper" \
  "sh -c 'git commit -am x'|sh -c wrapper" \
  "eval 'git commit --all -m x'|eval wrapper"; do
  cmd="${case_row%|*}"
  label="${case_row##*|}"
  has "$(guard_cmd "$cmd")" "separately staged plain commit" \
    "guard: unsafe commit mode $label is blocked"
  eq "$(guard_rc "$cmd")" "2" "guard: unsafe commit mode $label exits 2"
done
empty "$(guard_cmd "git commit -m 'message mentions --all and .env'")" \
  "guard: unsafe-looking words inside commit message are not options"
empty "$(guard_cmd "git commit --author 'Only Person' -m x")" \
  "guard: safe option values are not pathspecs"
empty "$(guard_cmd "git commit -qm x")" \
  "guard: combined safe commit options remain allowed"
empty "$(guard_cmd "git commit --amend --no-edit")" \
  "guard: amend without unstaged-content modes remains allowed"
empty "$(EVERGREEN_GUARD=off guard_cmd "git commit -am x")" \
  "guard: EVERGREEN_GUARD=off bypasses unsafe commit-mode rejection"
git -C "$TMP" checkout -q -- .env
git -C "$TMP" rm -q .env
git -C "$TMP" commit -qm "remove env fixture"

# A commit that DELETES slop is the guard doing its job — must never be blocked (--diff-filter=d).
printf 's\n' > "$TMP/AUDIT-2026-01-01.md"; git -C "$TMP" add -f AUDIT-2026-01-01.md
git -C "$TMP" commit -qm "add slop to be removed"
git -C "$TMP" rm -q AUDIT-2026-01-01.md   # stage a pure deletion of the slop file
empty "$(guard_cmd "git -C $TMP commit -m cleanup")" \
  "guard: deletion-only option-prefixed commit of slop -> allowed (cleanup enforced, not blocked)"
git -C "$TMP" commit -qm "remove slop"

# Release CI must gate the complete trust and host surface on both supported OS families.
workflow="$(cat "$ROOT/.github/workflows/test.yml")"
for token in \
  "ubuntu-latest" \
  "macos-latest" \
  "python3 -m unittest discover" \
  "bash tests/hooks.sh" \
  "bash tests/action.sh" \
  "eval/bench/run_bench.py --selftest"; do
  has "$workflow" "$token" "CI gates release surface: $token"
done

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
