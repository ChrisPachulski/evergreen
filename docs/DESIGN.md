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
The six signals in `bin/evergreen-scan` today, ranked by cost/reliability (mined
from agent B). The engine refuses to run outside a git repo (exits 1) rather than
report a false "clean":
1. **Doc-named path existence** — every file-like, in-repo path a doc names must
   exist on disk or be tracked. A path is only validated if it starts with a real
   top-level tracked dir and carries a known file extension (kills URL paths,
   framework refs, sibling-repo refs). Generic, no false-negative.
   *(credit: our own ReadmeAccuracyTests + lychee)*
2. **Rename cross-reference** — `git diff --name-status --find-renames` vs `--base`;
   docs that still cite a renamed/deleted path are confirmed drift. *(credit: agent-B synthesis)*
3. **Contract existence** — a `--word` CLI flag or `UPPER_SNAKE` env/config key that a
   doc documents but that no tracked non-doc file contains (both **medium** — a backticked
   token is lower-confidence than a missing file path; demoted from high after the
   first-usage report showed flags were the noisiest signal). Precision by construction:
   only tokens inside `inline-code` spans count (prose UPPER_SNAKE and markdown rules never
   reach the matcher), the underscore rule kills all-caps like JSON/HTTP/README, and
   existence is a whole-token boundary match so `--verbose` is not satisfied by
   `--verbose-mode`. Flag-shaped tokens that aren't *our* flags are dropped: CSS custom
   properties (`var(--x)`/`--x:`), another tool's flags (span starts with a known binary —
   `git …`, `docker …`; extend via `EXTERNAL_TOOLS`), and trailing-dash fragments. The
   residual long tail is suppressed per-repo via `IGNORE_FLAGS`/`IGNORE_ENV`/`IGNORE_DOCS`
   in `.evergreen.sh`. *(credit: Akshaysanthosh/docs-drift-check,
   killytoronto/drift-guardian, MarekWadinger/doc-checks)*
4. **Embed-from-source** — a fenced block pinned with
   `<!-- evergreen:embed path:Lstart-Lend -->` is checked against those source lines;
   a mismatch (or a missing source/range) is drift. `--fix` rewrites the block from
   source, so an embedded snippet structurally cannot silently lie.
   *(credit: ifiokjr/mdt)*
5. **SHA-pinned manifest** — `.evergreen-manifest` is TSV: whole-file
   `doc<TAB>source<TAB>blob-sha`, or region-pinned (method-level)
   `doc<TAB>source<TAB>Lstart-Lend<TAB>sha` where the sha hashes only the pinned lines.
   The sha is captured when the doc was last verified. When that content hashes
   differently the doc is flagged `needs_reverify` (medium) — not proven wrong, just no
   longer pinned; a range pin means edits *outside* the range don't trip it. `--fix`
   re-pins (preserving the range field); a missing source is high, a malformed range is
   `needs_reverify`. *(credit: os-tack/docfresh)*
6. **Runnable example** — a fenced block whose info string contains `evergreen` is
   executed; a nonzero exit is drift. Double-gated: doc-author tag AND operator
   `--run-examples` (never the Stop hook), run with a scrubbed env + scratch HOME. This
   is blast-radius reduction, not a sandbox — running doc code is inherently unsafe, so
   it is off by default and for trusted docs only. *(credit: georg-nikola/docs-drift)*

ROADMAP for this layer (designed, not yet coded):
- **AST fingerprint (opt-in)** — tree-sitter hash of a tracked symbol, stored as
  `sig:<hex>` in the doc's frontmatter anchor; whitespace/rebase-immune. The
  deterministic *line-range* pin (signal 5) is the method-level stand-in and is BUILT;
  a true AST hash (immune to whitespace/rebase and line-number churn) remains the
  upgrade. *(credit: danielhirt/kedge, NicoSchwandner/docdrift — the most robust binding found)*
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
guides the skill. The deterministic engine emits `in_docs_not_code` (a documented
thing the code lacks) and `needs_reverify` (a pinned manifest source moved under the
doc); the remaining categories are model-side.
*(credit: NathanMaine/memoriant-docforce + Tenormusica/doc-freshness-analyzer)*

Severity with an explicit **Auto-Fixable?** flag per finding. *(credit:
Zarl-prog/doc-drift-detector)*

### 4. Coverage as a defensible score — BUILT (parser-backed for py/js/ts/go/rust)
`--coverage` counts public symbols and whether each carries an adjacent doc-comment,
parser-backed where the toolchain is available and gracefully falling back to regex otherwise.
Every parser is a **single-file syntactic** parse — it does not resolve imports, so unresolved
`mod`/`use`/missing sibling files don't matter (the reason standalone `rustdoc`/`go doc`,
which build the crate/package, are unusable per-file):
- **Python** — stdlib `ast` when `python3` is present (functions/classes incl. `async def`,
  ignoring defs in comments/strings, excluding `_`-prefixed names incl. dunders).
- **JS/TS** — `deno doc --json` when `deno` + `python3` (to read deno's JSON) are present
  (exported functions/classes/interfaces/enums/types/variables + public class methods).
- **Go** — the stdlib `go/ast` parser via a `go build`-cached helper (`bin/helpers/go-cov.go`)
  when `go` is present (exported funcs/types/methods incl. grouped `type ( … )`, with doc
  resolved on the GenDecl or the TypeSpec).
- **Rust** — the `syn` parser (the de-facto Rust syntactic parser, what proc-macros use)
  via a cargo-built cached helper (`bin/helpers/rust-cov/`) when `cargo` is present (pub
  fn/struct/enum/trait/type + pub impl methods; `///`/`//!`/`#[doc]` = documented).

Each falls back to the regex heuristic when its toolchain is absent, the first build fails
(e.g. offline — the Rust helper fetches `syn` once), or a file won't parse. The helpers build
lazily, once, under `$XDG_CACHE_HOME/evergreen` (out-of-tree — nothing written to the repo)
and rebuild when the helper source changes. The human label reads
`[py: ast|regex; js/ts: deno|regex; go: ast|regex; rust: syn|regex]`. `--fail-under`
defaults to 80 and gates under `--ci` (exit 2), with **delta-gating**: `--coverage --fix`
records a `.evergreen-coverage` baseline, and dropping below it fails even when above
threshold (the ratchet). `--coverage --badge` writes/refreshes a shields.io markdown
badge between `<!-- evergreen:badge:start -->`/`<!-- evergreen:badge:end -->` markers in
README.md (idempotent; brightgreen ≥80, yellow ≥50, red else; with no markers it prints
the badge to stderr so `--json`/`--sarif` stdout stays valid).
*(credit: econchick/interrogate 667★, epassaro/docstr-cov-workflow)*

### 5. Safe auto-fix (the "keep fresh" half) — partially BUILT
The generate-vs-review line *(credit: agent-D synthesis across docugardener /
ArjunVenat / Sintesi)*:
- **BUILT — `--fix` (fully derivable only):** refresh an embed block from its source,
  re-pin a manifest sha, set the coverage baseline. These are mechanical and need no
  model. `--fix` **never** edits prose — fix messages go to stderr so `--json`/`--sarif`
  stdout stays clean.
- **BUILT — `--fix-prose` (model, scoped to dead-reference prose):** opt-in (requires
  the `claude` CLI). For docs the deterministic pass flagged with a **dead reference** —
  a file path, a CLI flag, or an `UPPER_SNAKE` env/config key the code no longer
  has — it asks the model for a minimal correction, then enforces three deterministic gates
  without trusting the model: (1) the draft no longer contains any flagged stale token,
  (2) it adds no net lines (kills injected preambles), and (3) every changed/removed original
  line carried a stale token (kills rewording of unrelated prose). An independent review-call
  (PASS) backs these up best-effort. Up to 3 retries recover from model
  variance; the gates guarantee a bad draft is never applied. A validated fix is written
  to the **working tree** and the diff printed — never committed; failures are left as
  `needs_review`. The `tests/golden-prose.sh` harness scores it with an LLM-free rubric
  (post-fix the finding is resolved, unrelated content preserved, no new drift), opt-in
  via `EVERGREEN_LLM_TESTS=1` and skipping cleanly without `claude`.
- **ROADMAP / human-only — what stays unfixed:** a changed (not absent) signature or
  type, and free-form rationale that explains *why* (architecture, tutorials, "how it
  works", security model). These have no deterministic anchor for the gates to check, so
  auto-fixing them is too unsafe — they remain flagged for a human. Extending the gate
  (temp-0 validator, PR output, golden-set CI, bot-loop prevention) to derivable
  signature/table fixes is the roadmap.

### 6. Persistence & reporting — BUILT
`--log FILE` appends each finding as one JSON object (a cross-session JSONL audit
trail) *(credit: memoriant-docforce)*; `--sarif` emits SARIF 2.1.0 for GitHub
code-scanning; `--score`/`--json` report a `freshness_pct` (100 minus a
severity-weighted penalty — high 15, medium 5, low 2; floored at 0, deterministic and
monotonic). ROADMAP: the opt-in local NLI judge and a learned **alignment score**
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
- **Noise blocklist + learnings ledger** *(sachn1/readme-drift, drift)* — now BUILT in the
  engine as the `IGNORE_DOCS`/`IGNORE_FLAGS`/`IGNORE_ENV`/`EXTERNAL_TOOLS` knobs in
  `.evergreen.sh`, plus the lead/freeze `EXEMPT` defaults (specs/plans + audit/dated snapshots).
- **Non-blocking Stop-hook nudge** *(Jan-ARN/drift)* — implemented in `hooks/`.

## Roadmap (designed, not yet in the engine)
The current `freshness_pct`, embed-from-source, SHA-pinned manifest (incl. region pins),
coverage (incl. the `--badge`), and the derivable-only `--fix` are BUILT (sections 1, 4,
5, 6 above). What is still designed-only:
- **Richer freshness score** — two-column own/link severity → project entropy, beyond
  today's flat severity-weighted penalty *(axiom-graph + docsentinel hard/soft split +
  Entropy-Meter)*.
- **AST-hash binding** — a tree-sitter context-node hash, so a pin survives line-number
  churn and reformatting; today's deterministic line-range pin is the stand-in
  *(NicoSchwandner/docdrift, kedge)*.
- **Broader model-drafted prose fixes** — extend the dead-reference `--fix-prose` gate
  (BUILT, section 5) to derivable signatures/tables; changed signatures and free-form
  rationale stay human-only (no deterministic anchor).
