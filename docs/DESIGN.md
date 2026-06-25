# Evergreen — design & prior-art credits

A documentation-freshness **companion to ponytail**: a global, ride-along reflex
that keeps vibe-coded docs honest in every project. Where ponytail asks *"does
this code need to exist?"*, evergreen asks *"does this doc still match the code?"*

This design is synthesized from a survey of **309 repos** (164 directly related,
79 of them zero-star — the clever-but-unknown longtail). Techniques are credited
to the repos they were mined from; nothing here is reinvented where an accredited
approach already exists.

## Principles (the ponytail parallel)

| ponytail | evergreen |
|---|---|
| laziest solution that works | **cheapest signal that catches the drift** — deterministic before LLM |
| `lite / full / ultra` | `off / warn / block` strictness |
| anti-over-engineering reflex | anti-doc-staleness reflex |
| runtime hooks | PreToolUse commit hook |

## Architecture: detect cheap → triage smart → fix safe

### 1. Deterministic detection first (zero LLM, runs everywhere) — BUILT
The four signals in `bin/evergreen-scan` today, ranked by cost/reliability (mined
from agent B). The engine refuses to run outside a git repo (exits 1) rather than
report a false "clean":
1. **Doc-named path existence** — every file-like, in-repo path a doc names must
   exist on disk or be tracked. A path is only validated if it starts with a real
   top-level tracked dir and carries a known file extension (kills URL paths,
   framework refs, sibling-repo refs). Generic, no false-negative.
   *(credit: our own ReadmeAccuracyTests + lychee)*
2. **Rename cross-reference** — `git diff --name-status --find-renames` vs `--base`;
   docs that still cite a renamed/deleted path are confirmed drift. *(credit: agent-B synthesis)*
3. **Contract existence** — a `--word` CLI flag (high) or `UPPER_SNAKE` env/config
   key (medium) that a doc documents but that no tracked non-doc file contains. Plain
   Precision by construction: only tokens inside `inline-code` spans count (prose
   UPPER_SNAKE and markdown rules never reach the matcher), the underscore rule kills
   all-caps like JSON/HTTP/README, and existence is a whole-token boundary match so
   `--verbose` is not satisfied by `--verbose-mode`. *(credit: Akshaysanthosh/docs-drift-check,
   killytoronto/drift-guardian, MarekWadinger/doc-checks)*
4. **Runnable example** — a fenced block whose info string contains `evergreen` is
   executed; a nonzero exit is drift. Double-gated: doc-author tag AND operator
   `--run-examples` (never the Stop hook), run with a scrubbed env + scratch HOME. This
   is blast-radius reduction, not a sandbox — running doc code is inherently unsafe, so
   it is off by default and for trusted docs only. *(credit: georg-nikola/docs-drift)*

ROADMAP for this layer (designed, not yet coded):
- **AST fingerprint (opt-in)** — tree-sitter hash of a tracked symbol, stored as
  `sig:<hex>` in the doc's frontmatter anchor; whitespace/rebase-immune.
  *(credit: danielhirt/kedge — the most robust binding found)*
- A **staleness spectrum** (`src.mtime − doc.mtime` → fresh/getting-stale/stale/
  rotten, *credit: e4we/doc-staleness*) was prototyped and **dropped**: age is a weak
  proxy — an old doc can still be true, and a freshly-touched one can still lie.
  Evergreen flags only what it can prove against the code.

### 2. LLM triage only on confirmed candidates — model-side (skill, not engine)
Never ask an LLM to *detect* what grep/git already knows. The deterministic pass
emits a candidate list; an LLM classifies **severity** only. This is rung 4 of the
freshness ladder and lives in the skill, not the binary. *(credit: kedge +
deichrenner/driftcheck hybrid)*

Model routing: Haiku for mechanical work, Sonnet/Opus for semantic behavior drift.
*(credit: xiaolai/docs-guardian)*

### 3. Drift taxonomy (so findings are actionable) — partially BUILT
The full taxonomy `in_code_not_docs · in_docs_not_code · name_mismatch · UNVERIFIABLE`
guides the skill. The deterministic engine emits only `in_docs_not_code` today (a
documented thing the code lacks); the other categories are model-side.
*(credit: NathanMaine/memoriant-docforce + Tenormusica/doc-freshness-analyzer)*

Severity with an explicit **Auto-Fixable?** flag per finding. *(credit:
Zarl-prog/doc-drift-detector)*

### 4. Coverage as a defensible score — ROADMAP
tree-sitter universal query — *public symbol* + *immediately-preceding doc-comment*,
per-language. `fail_under` default 80, README badge, and **delta-gating** (block a
PR that *drops* coverage even if above threshold — the ratchet). *(credit:
econchick/interrogate 667★, epassaro/docstr-cov-workflow)*

### 5. Safe auto-fix (the "keep fresh" half) — ROADMAP
The generate-vs-review line *(credit: agent-D synthesis across docugardener /
ArjunVenat / Sintesi)*:
- **Auto-fixable** (1:1 derivable from code): signatures, param lists, endpoint
  tables, type/enum/config schemas, dead path references.
- **Never auto-fix** (prose/intent): architecture rationale, tutorials, "how it
  works", security model.
- **Gate**: two-pass — generator drafts, temperature-0 validator must pass; failures
  downgrade to `needs_review`, never silently dropped.
- **Output**: a PR/diff by default, not a silent commit. Bot-loop prevention.
- **CI**: golden-dataset regression scored by an LLM-free rubric (no API spend in CI).

### 6. Persistence & reporting — ROADMAP
JSONL audit log of drift across sessions *(credit: memoriant-docforce)*; SARIF
output for GitHub code-scanning; an **alignment score** with a CI regression gate
*(credit: Arthur920/Staleguard — deterministic core + opt-in local NLI judge)*.

## What stays homegrown
The **semantic claim assertions** ("this prose fact about the code still holds")
remain project-specific — no off-the-shelf tool owns your repo's knowledge. That's
the slice our own assertion tests (and the LLM triage) fill.

## Folded into the skill (mining agents E + A2)
- **Six-lens rot taxonomy** *(Jan-ARN/drift)* — contradiction / stale-reference /
  signature-mismatch / outdated-example / resolved-marker / orphaned-comment.
- **Pre-filter before the model + adversarial verify** *(Jan-ARN/drift)* — only
  candidates near changed hunks reach a model; a skeptic must cite code or the flag drops.
- **"Editing is not verification" (sticky staleness)** *(ddpoe/axiom-graph)*.
- **"Code is the source of truth, doc is the claim"** asymmetry *(MarekWadinger/doc-checks)*.
- **Noise blocklist + learnings ledger** *(sachn1/readme-drift, drift)*.
- **Non-blocking Stop-hook nudge** *(Jan-ARN/drift)* — implemented in `hooks/`.

## Roadmap (designed, not yet in the engine)
- **Defensible freshness score** — two-column own/link severity → project entropy →
  `freshness_pct` *(axiom-graph + docsentinel hard/soft split + Entropy-Meter)*.
- **SHA-pinned source→doc manifest** with `verified_at:{sha}` + three-tier auto-suggest
  *(os-tack/docfresh)*; method-level AST-hash context nodes *(NicoSchwandner/docdrift, kedge)*.
- **Embed-from-source** snippets that structurally cannot drift *(ifiokjr/mdt)*.
- **Coverage score** — tree-sitter public-symbol + adjacent-doc-comment, `fail_under`
  80, badge, delta-gating ratchet *(interrogate, docstr-cov-workflow)*.
- **Safe auto-fix** — derivable-only diffs, temp-0 validator, PR output, golden-set CI.
