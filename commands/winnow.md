---
description: Winnow documentation against the code — the deep prove-true pass. Walk every documented claim, certify it true or surface it; separate certified grain from drift. Never gate exempt docs.
---

Run an evergreen **winnow** using the **evergreen skill** — the deep freshness pass. A reasoning
pass, not a tool run: do the checking with your own tools (read, grep, diff) and cite the code for
every finding.

**Acceptance bar (non-negotiable).** Before you report, satisfy *every* MUST in
[`skills/evergreen/hard-goals/winnow.md`](../skills/evergreen/hard-goals/winnow.md). A run that fails
one — the claim count isn't enumerated from a shown command, a `certified` row lacks a code
`file:line`, a `drift` isn't proven gone by grep, or the counts don't sum to N — is **not done**,
however good it reads. Those checks are re-runnable by anyone; they are the bar, not your judgment.

Where the reflex only flags lies it can cite, winnow is **affirmative**: walk *every* documented
claim and either **certify** it (cite the code that makes it true) or **surface** it. Post-winnow
silence = every claim certified, not "no lie found". Always runs at **strict** depth (all four rungs)
regardless of the repo's ambient mode.

Scope: changes since `${1:-origin/main}` (diff against that ref); if it doesn't exist, winnow the
docs against the current tree.

Walk the ladder, cheapest rung first, stop reporting at the first that holds:

1. **Vanished paths** — every in-repo path a doc names must still exist on disk (or be tracked).
   Renamed/deleted in the diff but still cited = drift.
2. **Dead contracts** — every CLI flag (`--word`), env/config key (`UPPER_SNAKE`), function, route,
   or type a doc documents must still exist in the code. Grep it.
3. **Drifted snippets/signatures** — fenced blocks, signatures, endpoint tables, config schemas that
   no longer match the source. Read both, compare.
4. **Semantic drift** — does the prose still describe what the code does? Reason with the code in
   front of you.

**Put the judgment on trial (rungs 3–4).** A judgment-call flag is not a finding until it survives
the skill's shared harness, "Put the verdict on trial". Winnow's parameters: the **claim** is the
doc's assertion against the current code; the **verdict space** is drift / fine / `unverified`; the
**prongs** are *defend* (the reading that makes it hold) / *prove-wrong* (the exact code token that
breaks it) / *hardest-broken* (the airtight case for drift). Snap, challenge, three blind reads,
blind-spot, weigh — a drift ships only if the accusation beat its strongest defense.
Where the code runs, **prove by test is the default** for behavioral claims — write the smallest
test that encodes what the *doc* claims and run it: passes → certified by test, fails →
drift-proven-by-execution, won't run → inconclusive, fall back to `verify manually` (never flag on
a test you don't trust). `--prove-by-test` forces it on a bare CLI. The test is scratch; show it,
don't commit it.

**Affirmative verification** — every claim is left in one of three states, never silently passed:
- **certified** — you read the doc passage and the current code and they match; cite the code.
- **drift** — a finding (a category below); cite the code that makes it wrong.
- **`unverified — <why>`** — a precise behavioral claim you can't settle (ordering/timing/"returns
  empty on miss"). Surface as `behavior-asserted — verify manually`. Reported, not dropped. Distinct
  from `UNVERIFIABLE` (a claim about another system — still dropped).

For each surviving drift, classify: category (`in_code_not_docs` / `in_docs_not_code` /
`name_mismatch` / `UNVERIFIABLE`), severity, and whether it's a derivable fix or a human-judgment
flag.

Report verdict-first, one line per finding:
`[sev] category  file:line — what's wrong (cited) → fix or flag`.
For derivable drift (dead references, endpoint/type/config tables, a snippet that should mirror its
source) propose a minimal diff. For prose/intent/signature drift, flag — never rewrite.

Do NOT flag: exempt docs (specs/ADRs/roadmaps/CHANGELOG history/dated snapshots), `UNVERIFIABLE`
claims about other systems, or anything you cannot cite code for.
