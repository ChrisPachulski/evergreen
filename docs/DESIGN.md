# Evergreen — design & prior-art credits

Evergreen is a local semantic **skill** that makes the agent *notice* when docs and code have
drifted apart and prove it before flagging. A deterministic trust layer supports that judgment in
CI; it does not replace it with a general-purpose scanner or hosted service. The semantic work
still uses the model's optional local evidence (read, grep, diff, and scratch tests when needed).

This design is synthesized from a survey of **309 repos** (164 directly related, 79 of
them zero-star — the clever-but-unknown longtail). The repos were an **idea mine**: we
took techniques, taxonomies, and mental models and distilled them into the skill's
ruleset. Nothing here is reinvented where an accredited approach already exists; each
idea is credited to the repo it was mined from.

> **Design history (honest note):** an earlier iteration built a deterministic bash
> engine (grep/git path & contract verdicts, tree-sitter-style coverage, SARIF). That verdict
> engine was removed: it ported the *infrastructure* of the surveyed repos when the goal was to
> mine their *ideas* for the prompt. Today's smaller deterministic layer prepares and validates
> evidence; semantic truth judgments remain in the skill rather than a binary.

## Principles

- **Cheapest check that proves the drift** — mechanical rungs before semantic reasoning.
- **Intensity `off / light / strict`** — light walks rungs 1–3, strict adds the rung-4 semantic pass.
- **A local anti-doc-staleness reflex**, injected as behavior, with a deterministic CI boundary.

## The freshness ladder (the core behavior)

When code changes, the agent walks the rungs in order and stops at the first that holds.
The cheap, mechanical checks come before the semantic read — but the *agent* does them,
with the tools it already has (read the file, grep the repo, read the diff), citing the
code every time.

1. **Vanished paths** — an in-repo file path a doc names that no longer exists on disk,
   or was just renamed/deleted in the diff but is still cited. *(kedge, docs-drift-check, lychee)*
2. **Dead contracts** — a CLI flag, env/config key, function, route, or type a doc
   documents that no longer exists in the code. Code is the source of truth; the doc is
   the claim under test. *(doc-checks, readme-drift, sachn1)*
3. **Drifted snippets/signatures** — a fenced code block, signature, endpoint table, or
   config schema in a doc that no longer matches the source it describes. *(ifiokjr/mdt, docfresh)*
4. **Semantic drift** — only then: does the prose still describe what the code does now?
   Reasoned, with the code in front of you. *(driftcheck/kedge hybrid)*

Model routing, for hosts that tier it: cheap models for the mechanical rungs, stronger
ones for semantic behavior drift. *(xiaolai/docs-guardian)*

## Architecture (skill + trust layer + hooks + state)

The intelligence is the skill (`skills/evergreen/SKILL.md`) — the ladder and rules live in the
model's head. Three thin hooks make it ride along, and they never read or analyze doc *content*:

- **`evergreen-activate.sh`** (SessionStart) injects the mode preamble plus the condensed
  `DIGEST.md` (~⅓ the tokens of the full skill, which stays loadable on demand).
- **`evergreen-mode-tracker.sh`** (UserPromptSubmit) is the *sole writer* of the intensity state.
- **`evergreen-stop.sh`** (Stop) is a post-turn audit request when code-with-docs changed; git/state
  guards only, always non-blocking, deduped to fire once per distinct change state (a signature in
  `.git/`, not on every turn while the tree sits dirty).

State is a per-repo `.evergreen-mode` file (`off|light|strict`, default `light`, gitignored). An
optional repo-local `.evergreen-ignore` lists patterns the *agent* honors when deciding what to
flag — there is no hook that parses it; the skill is the enforcement.

The PR Action adds a deterministic trust layer around the semantic reviewer. It produces a bounded
manifest for exact base/head commits, JSON-escapes boundary characters, labels all repository
material as **untrusted data**, and rejects any result that fails schema, count, commit, citation,
or trusted-runtime checks. The validator reads citations from Git at the audited head; model prose
cannot certify itself. The layer prepares and validates evidence but does not decide whether prose
is semantically true.

CI outcomes are deliberately distinct: **complete and clean**, **complete with findings**, and
**complete with unverified** all mean the validated review finished; only the first is a clean
certification. **inconclusive** means the audit itself failed or could not be trusted. Findings
never fail the check. Inconclusive runs fail by default, while `fail_on_inconclusive: false` makes
infrastructure advisory without changing the rendered status.

The commit-time hygiene guard has a narrower boundary. It inspects the finalized staged index on a
commit-only call, blocks known secret/slop paths, and allows deletion-only cleanup. A single shell
call that combines staging and commit is rejected conservatively because PreToolUse cannot observe
the index between them: use **separate tool calls**. `EVERGREEN_GUARD=off` remains the explicit
bypass. Semantic truth findings and CI drift findings do not use this blocking path.

## Release identity boundary

Release identity spans package manifests, registry versions, and version-reporting CLI output.
Audit version-bearing badges, version-reporting installed-command examples, generated API version labels or headers, and deployed docs version labels as linked release claims.
Interpret each claim's meaning: current source and latest published release may legitimately differ.
Keep independently versioned packages and platforms as independent release streams unless repository policy explicitly couples them.
Without direct registry, store, or deployment evidence, report external release state unverified.
Never publish, upload, push, deploy, or mutate a portal or registry without explicit user authority.

The checked-in manifest owns current-source identity unless the repository declares another source.
A version-bearing surface must also declare what it means. Compare current-source claims with the
manifest and latest-published claims with direct registry/store/deployment evidence; do not call
their expected difference drift. Generated API documentation is reconciled through its generator
or source, not silently hand-edited. A local tag, badge, or release note cannot certify public state.

Monorepos need an explicit coupling rule. Related deliverables in one stream align; independently
published packages do not receive synchronized bumps merely because they share a repository. Apple
apps retain the distinct marketing-version/build-number rules, monotonic binary builds, Universal
Purchase alignment, and external-store uncertainty described by the skill.

## Hybrid provider boundary

Provider evidence and source maps nominate candidates, never findings or verdicts.
Re-read every candidate against current code before deciding drift.

The read-only `bin/evergreen impact [--repo PATH] [--evidence FILE] [--json] PATH...` command
combines changed paths, validated provider facts, and repository-local source maps into a ranked
candidate list. Provider confidence affects ranking only. A map broadens the candidate set; it
cannot suppress changed-path or normal grep baseline candidates, and it cannot certify a document.
Invalid records and maps remain warnings rather than semantic conclusions.

This boundary supports Drift-shaped interoperability through adapters that translate mechanical
facts into the v1 evidence schema. It deliberately does not import an external tool's finding or
verdict. The fixture demonstrates why: a default changing from 60 to 30 can nominate timeout docs,
but the per-project override remains true because `setdefault` preserves configured values.

## Drift taxonomy (so findings are actionable)

Every finding is one of `in_code_not_docs · in_docs_not_code · name_mismatch ·
UNVERIFIABLE` (a claim about another system — drop it, don't guess). *(NathanMaine/memoriant-docforce,
Tenormusica/doc-freshness-analyzer)*

Prose/comment rot lenses, each verifiable against the code: `contradiction ·
stale-reference · signature-mismatch · outdated-example · resolved-marker ·
orphaned-comment`. *(Jan-ARN/drift)*

Each finding carries a severity and an explicit **fix-or-flag** call. *(Zarl-prog/doc-drift-detector)*

## Rules that keep it trusted

- **Prove it or drop it** — cite the code, or it isn't a finding; an adversarial second
  look kills plausible-but-wrong flags. *(Jan-ARN/drift skeptic pass)*
- **Rot lives in old comments, not new lines** — read the changed file at HEAD, not just
  the diff's `+` lines. *(Jan-ARN/drift)*
- **Editing is not verification** — a file touch must not reset staleness; only a check
  against the code clears it. *(ddpoe/axiom-graph: sticky staleness)*
- **Code is the source of truth, the doc is the claim** — documented-but-missing is
  failure; existing-but-undocumented is informational. *(MarekWadinger/doc-checks)*
- **Exempt what leads or freezes code** — specs/ADRs/RFCs/roadmaps/plans lead; audit/
  readiness/archive/dated snapshots and CHANGELOG history freeze. Never gate either.
- **Noise blocklist + learnings ledger** — third-party tool flags, CSS custom properties,
  URLs, generic symbols are not your contracts; a rejected flag never returns.
  *(sachn1/readme-drift, drift)*

## The fix half — generate vs review

The generate-vs-review line *(docugardener / ArjunVenat / Sintesi synthesis)*:

- **Propose a diff** for what's 1:1 derivable from code — a dead reference (renamed/removed
  path, flag, env key), an endpoint table, a type/enum/config schema, a snippet that
  should mirror its source. The human applies it.
- **Flag, never rewrite** what has no deterministic anchor — a changed signature,
  architecture rationale, tutorials, "how it works", the security model, the *why*.

## What stays homegrown

The **semantic claim assertions** ("this prose fact about the code still holds") are
project-specific — no off-the-shelf idea owns your repo's knowledge. That's the slice
the model's judgment fills, guided by everything above.

## Source ideas, in brief

Mining notes live under `.research/` (gitignored). Beyond the credits inline above:
six-lens rot taxonomy and pre-filter-before-the-model *(Jan-ARN/drift)*; "code is truth,
doc is claim" asymmetry *(MarekWadinger/doc-checks)*; sticky staleness *(ddpoe/axiom-graph)*;
coverage-as-a-score thinking *(econchick/interrogate, epassaro/docstr-cov-workflow)*;
embed-from-source and SHA-pinning as the *concepts* a human can apply by hand
*(ifiokjr/mdt, os-tack/docfresh)*; staleness-by-age was evaluated and **rejected** — age
is a weak proxy; evergreen flags only what it can prove against the code *(e4we/doc-staleness)*.
