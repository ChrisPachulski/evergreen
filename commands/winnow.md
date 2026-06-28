---
description: Winnow documentation against the code — the deep prove-true pass. Walk every documented claim, certify it true or surface it; separate certified grain from drift. Never gate exempt docs.
---

Run an evergreen **winnow** using the **evergreen skill** — the deep freshness pass. This is a
reasoning pass, not a tool run: you do the checking with your own tools (read files, grep the
repo, read the diff), and you cite the code for every finding.

Winnowing separates true grain from chaff. Where the ride-along reflex only flags the lies it
can cite, winnow is **affirmative**: walk *every* documented claim and either **certify** it
(cite the code that makes it true) or **surface** it. Silence after a winnow means *every claim
was certified* — not merely "no lie found." This pass always runs at **strict** depth (all four
rungs, full rung-4 semantic prose pass) regardless of the repo's ambient `/evergreen` mode.

Scope: changes since `${1:-origin/main}` (diff against that ref); if it doesn't exist, winnow
the docs against the current tree.

Walk the freshness ladder, cheapest rung first, and stop reporting at the first that holds:

1. **Vanished paths** — every in-repo file path a doc names must still exist on disk
   (or be tracked). Grep the docs for paths, confirm each. Renamed/deleted in the diff
   but still cited = drift.
2. **Dead contracts** — every CLI flag (`--word`), env/config key (`UPPER_SNAKE`),
   function, route, or type a doc documents must still exist in the code. Grep for it.
3. **Drifted snippets/signatures** — fenced code blocks, signatures, endpoint tables,
   and config schemas that no longer match the source they describe. Read both, compare.
4. **Semantic drift** — only now: does the prose still describe what the code does?
   Reason with the code in front of you.

**Affirmative verification (what makes this a winnow, not a flag-pass):** every claim is left in
one of three states, not silently passed —
- **certified** — you read the doc passage and the current code and they match; cite the code.
- **drift** — a real finding (one of the categories below); cite the code that makes it wrong.
- **`unverified — <why>`** — you could not settle it against the code (a precise behavioral
  claim about ordering/timing/"returns empty on miss"). Surface it as `behavior-asserted —
  verify manually`. This is reported, not dropped. (Distinct from `UNVERIFIABLE` — a claim about
  *another system* — which is still dropped, never guessed.)

For each surviving drift finding, classify: category (`in_code_not_docs` / `in_docs_not_code`
/ `name_mismatch` / `UNVERIFIABLE`), severity, and whether it's a derivable fix or a
human-judgment flag.

Report verdict-first, one line per finding:
`[sev] category  file:line — what's wrong (cited) → fix or flag`.
For derivable drift (dead references, endpoint/type/config tables, a snippet that should
mirror its source) propose a minimal diff. For prose/intent/signature drift, flag for
review — never rewrite it.

Do NOT flag: exempt docs (specs/ADRs/roadmaps/CHANGELOG history/dated snapshots),
`UNVERIFIABLE` claims about other systems, or anything you cannot cite code for.
