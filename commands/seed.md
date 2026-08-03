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
one — the inventory isn't surface-shaped, a prose claim lacks its ledger row, the seeded output
wasn't winnowed, the write set exceeds its budget, the diff deletes an existing doc line — is
**not done**, however good it reads. Those checks are re-runnable by anyone; they are the bar, not your
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

**B · Surface inventory.** Seed proceeds only from a complete, surface-shaped provider result. Each
candidate must include `symbol · kind · declaration code path:line · impact rank`, and the provider
must report the number of source files scanned. A path-only result, missing scan count, or any
truncation warning is `not done — surface inventory unavailable`; write nothing. (The provider is
`python3 "${CLAUDE_PLUGIN_ROOT}/bin/evergreen" gaps --json --repo <repo> [path…]` — deterministic
and read-only; a scope path narrows the inventory. Candidates are nominations, never verdicts.)

**C · Gap triage — walk until the ranking goes cold.** Enumerate the exact tracked living-document
path set `D`; in-code docstrings remain informational until a syntax-aware gap check exists. If `D`
is empty, skip the grep entirely — `git grep … --` with no pathspec searches the whole tracked tree
and would mark every symbol "documented" by its own declaration; with no living docs, every
candidate is undocumented by definition. Otherwise walk candidates in provider order and run:
```sh
git grep -F -w -n -e "<symbol>" -- <D...>
```
(`-w` is required: without word boundaries a symbol like `main` is "documented" by any prose
containing `remains` or `domain`.) A hit means `documented` only when at least one matched line
names the symbol **as code** — inline backticks, inside a fenced block, or with a `path:line`
citation. A bare prose reuse of the word ("the main branch", "a cache key") documents nothing;
if every hit is prose-only, the candidate stays a gap. Zero hits nominate a gap; read the
declaration and judge it. A gap is `worthy` only when a doc for it would carry at least one fact a
reader could not guess from the name and signature — a hidden rule, an error case, a default, a
side effect, an ordering requirement. Otherwise `not worthy`; `seed:gap` never satisfies this
floor. There is no fixed write budget: the worthy list is as long as the evidence makes it. Keep
walking until the ranking goes cold — 10 consecutive `documented`/`not worthy` verdicts — then
mark the remainder `deferred — ranking cold at rank R` and stop; the deferral is stated, never
silent.

**C2 · The owner picks the batch.** Before writing anything, present the full worthy list —
symbol, rank, one line on what its doc would say — plus a plain recommendation: which to write
first and why ("14 worth documenting; I'd start with these 6 — the CI contract surfaces — and
leave the eval internals for a later pass"). The owner chooses the count; the run never writes
past that choice, and no answer means write nothing. Writing zero is a valid outcome of a correct
run.

**D · Write, claim-disciplined.** Prose where the code backs it; each declarative sentence logged in
a **claim ledger** with the code `file:line` that makes it true. What the code can't settle —
intent, rationale, roadmap — becomes an explicit grep-able marker:
`<!-- seed:gap — author: why does X exist -->`. A marker is a finding for the author, not a failure;
an invented rationale is the failure. Names, signatures, parameter or field lists, and return types
may support a useful explanation, but restating them is not itself documentation worth seeding.
Keep each written candidate to at most 60 added lines and one `seed:gap` — the wall-of-text guard
is per-doc; the batch is bounded by the owner's chosen count, not by a fixed line total.

**E · Certify — winnow at birth.** Run the winnow ladder (all four rungs) over the seeded docs
before proposing them, exactly as `/evergreen:winnow` would on changed docs — newly created docs
*are* changed docs. Judgment-call verdicts go through winnow's trial verbatim (same claim space,
same prongs); `seed:gap` slots are exempt as marked. Required result: every ledgered claim
`certified`, zero `drift`, zero `unverified`. A claim that won't certify is cut or markered — the
doc bends to the code, never the reverse.

**F · Propose.** Emit new files / purely additive diffs for approval. Never rewrite existing prose,
never auto-commit. Nothing lands until the owner approves the batch.

## Put the worthiness verdict on trial (before any approved candidate is written)

The mechanical evidence never stands trial — the provider inventory, the pre-diff grep, the line
counts are facts. The **conclusion drawn from them** does: "this gap is worth a doc — write it" is
seed's judgment call, and a wrong YES is the over-population failure this command exists to avoid.
The pass-C walk judges cheaply; the trial runs on each **owner-approved** write (never the
deferred tail) before pass D touches it, through the skill's shared harness, "Put the verdict on
trial", with seed's parameters:

- **claim / snap:** "this undocumented gap supports a concrete reader use the declaration/signature
  alone can't recover — write it."
- **challenge (must survive):** "no — everything this doc would say is recoverable from the
  declaration alone, or the reader use is speculative, not concrete." A "write it" that can't beat
  its challenge is demoted to `not worthy` and reported back to the owner with the reason.
- **three blind reads:** *defend* the write (the reader use and the behavior-bearing fact, at its
  code `file:line`, that earns it — concede if the strongest case is signature-restatement) /
  *prove-unworthy* (show that each fact the doc would carry is recoverable from the
  declaration/signature, or say "one is not: <file:line>") / *hardest-noise* (the airtight case
  this doc is bloat that will cost readers more than it gives — concede if it isn't airtight).
- **blind-spot (the money one here):** "did all three miss that this candidate is *already
  documented* — under an alias, a renamed wrapper, or prose the fixed-string grep can't see?" A hit
  routes the candidate back to `documented`; it is never written.

The certification side needs no second harness: pass E already runs the seeded claims through
winnow's trial verbatim. Between the two, every judgment in seed stands trial — worthiness before
the budget is spent, truth before the proposal ships — while the mechanical goals in
`hard-goals/seed.md` skip it, as facts always do.

## Output

1. The provider command and its N surface-shaped candidates (pass B, verbatim), with the scanned
   source-file count.
2. The gap table —
   `candidate · rank · pre-diff grep · documented | worthy | not-worthy | deferred · reader-use claim · code file:line`
   — followed by the worthy list, the recommendation, and the owner's chosen count.
3. Per seeded doc: the proposed content, its claim ledger (`claim · code file:line`), and the
   winnow verdict counts (`certified == ledger rows, drift 0, unverified 0`).
4. The `seed:gap` markers, listed — the author's to-do, stated plainly (at most one per written
   candidate).
5. Coverage: `documented + worthy + not-worthy + deferred == N`, `written == owner-approved ≤
   worthy`, and every doc within the 60-line per-doc bound — stated with the numbers.
