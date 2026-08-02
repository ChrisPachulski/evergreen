---
description: "Seed documentation where the ground is bare — inventory the undocumented public surface, rank it by impact, write claim-disciplined docs certified by winnow at birth. Purely additive: proposes new docs, never rewrites prose, never invents what the code can't back."
---

Run **seed** using the **evergreen skill** — the creation axis. The family's creed is fewer, truer
claims; seed is the sanctioned exception on the *creation* side — you were invoked to **write docs
where none exist**, so write them. The discipline comes along: every sentence is a code-backed
claim with a citation, or an explicit marked gap. Never invented prose.

Argument: `{{args}}`. Default scope: the whole repo; an optional path narrows it.

**Acceptance bar (non-negotiable).** Before you report, satisfy *every* MUST in
[`skills/evergreen/hard-goals/seed.md`](../skills/evergreen/hard-goals/seed.md). A run that fails
one — the candidate set isn't enumerated from a shown impact command, a prose claim lacks its
ledger row, the seeded output wasn't winnowed, the diff deletes an existing doc line — is **not
done**, however good it reads. Those checks are re-runnable by anyone; they are the bar, not your
judgment.

## Forbidden shortcuts (take any one and you have NOT run seed)

1. **Ranking by taste.** "Document everything public" buries what matters; "document what looks
   important" is your opinion, not evidence. The candidate set comes from the impact provider,
   command shown.
2. **Inventing prose.** A sentence about intent, rationale, or roadmap the code can't settle is not
   filler — it is fabrication. Marker it (`<!-- seed:gap — author: … -->`), never write it.
3. **Shipping without the winnow.** Docs are claims from the moment they exist. Seeded docs never
   certified are drift *you created*.
4. **Touching existing prose.** Rewriting is flourish; removing is cultivate. Seed's diff is purely
   additive — one deleted doc line and the run has left its lane.

## Mandatory passes — run all; each leaves its evidence in the output

**A · Convention detection.** Detect the repo's doc grain before writing a word: README sections, a
`docs/` tree, in-code docstrings — cite what exists (`ls`, the README's structure). Extend the
grain you find. Bare repo fallback: README sections for a small surface, `docs/` pages for a large
one.

**B · Surface inventory.** Rank the reachable public surface with the existing provider:
```sh
python3 <plugin-root>/bin/evergreen impact --json <scope>
```
Candidates ordered as returned; `warnings` presented separately. Candidates are nominations, never
verdicts — the provider proves reachability, not doc-worthiness.

**C · Gap cross-reference.** For each ranked candidate, grep the doc set for the symbol:
```sh
git grep -n "<symbol>" -- '*.md' 'docs/'
```
Zero mentions → undocumented candidate. Aliased or prose-only mentions won't grep — where the hits
look wrong, read the doc before deciding. This in-prompt cross-reference is a deliberate ceiling;
the upgrade path is a deterministic `gaps` subcommand beside `impact`, when seed earns CI
integration. Write the top slice (impact-ordered); list the long tail as **informational**, never
silently dropped.

**D · Write, claim-disciplined.** Prose where the code backs it; each declarative sentence logged in
a **claim ledger** with the code `file:line` that makes it true. What the code can't settle —
intent, rationale, roadmap — becomes an explicit grep-able marker:
`<!-- seed:gap — author: why does X exist -->`. A marker is a finding for the author, not a failure;
an invented rationale is the failure.

**E · Certify — winnow at birth.** Run the winnow ladder (all four rungs) over the seeded docs
before proposing them, exactly as `/evergreen:winnow` would on changed docs — newly created docs
*are* changed docs. Judgment-call verdicts go through winnow's trial verbatim (same claim space,
same prongs); `seed:gap` slots are exempt as marked. Required result: every ledgered claim
`certified`, zero `drift`, zero `unverified`. A claim that won't certify is cut or markered — the
doc bends to the code, never the reverse.

**F · Propose.** Emit new files / purely additive diffs for approval. Never rewrite existing prose,
never auto-commit. Nothing lands until the owner approves the batch.

## Output

1. The impact command and its N ranked candidates (pass B, verbatim).
2. The gap table — `candidate · impact rank · documented? (grep shown) · written | informational`.
3. Per seeded doc: the proposed content, its claim ledger (`claim · code file:line`), and the
   winnow verdict counts (`certified == ledger rows, drift 0, unverified 0`).
4. The `seed:gap` markers, listed — the author's to-do, stated plainly.
5. Coverage: `written + informational == N`, stated with the numbers.
