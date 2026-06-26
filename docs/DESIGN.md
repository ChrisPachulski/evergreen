# Evergreen — design & prior-art credits

Evergreen is a **skill**, the documentation-freshness companion to **ponytail**. Where
ponytail is a prompt that makes the agent *write* like a lazy senior dev, evergreen is a
prompt that makes the agent *notice* when the docs and the code have drifted apart. No
scanner, no service — the intelligence is the model, exactly as in ponytail.

This design is synthesized from a survey of **309 repos** (164 directly related, 79 of
them zero-star — the clever-but-unknown longtail). The repos were an **idea mine**: we
took techniques, taxonomies, and mental models and distilled them into the skill's
ruleset. Nothing here is reinvented where an accredited approach already exists; each
idea is credited to the repo it was mined from.

> **Design history (honest note):** an earlier iteration built a deterministic bash
> engine (grep/git path & contract checks, tree-sitter-style coverage, SARIF/CI). It was
> removed: it ported the *infrastructure* of the surveyed repos when the goal was to mine
> their *ideas* for the prompt. Evergreen is the skill; the heuristics below live in the
> model's head, not in a binary.

## Principles (the ponytail parallel)

| ponytail | evergreen |
|---|---|
| laziest solution that works | **cheapest check that proves the drift** — mechanical before semantic |
| `lite / full / ultra` | `off / light / strict` — light walks rungs 1–3, strict adds rung-4 |
| anti-over-engineering reflex | anti-doc-staleness reflex |
| a skill, injected as behavior | a skill, injected as behavior |

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

## Architecture (skill + hooks + state)

The intelligence is the skill (`skills/evergreen/SKILL.md`) — the ladder and rules live in the
model's head. Three thin hooks make it ride along, and they never read or analyze doc *content*:

- **`evergreen-activate.sh`** (SessionStart) injects the mode-filtered ruleset as session context.
- **`evergreen-mode-tracker.sh`** (UserPromptSubmit) is the *sole writer* of the intensity state.
- **`evergreen-stop.sh`** (Stop) is a post-turn audit request when code-with-docs changed; git/state
  guards only, always non-blocking.

State is a per-repo `.evergreen-mode` file (`off|light|strict`, default `light`, gitignored). An
optional repo-local `.evergreen-ignore` lists patterns the *agent* honors when deciding what to
flag — there is no hook that parses it; the skill is the enforcement.

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
  *(ponytail: specs lead code)*
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
