---
description: Audit documentation freshness against the code — walk the evergreen freshness ladder, prove-or-drop, never gate exempt docs.
---

Run an evergreen documentation-freshness audit using the **evergreen skill**. This is
a reasoning pass, not a tool run — you do the checking with your own tools (read files,
grep the repo, read the diff), and you cite the code for every finding.

Scope: changes since `${1:-origin/main}` (diff against that ref); if it doesn't exist,
audit the docs against the current tree.

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

For each surviving finding, classify: category (`in_code_not_docs` / `in_docs_not_code`
/ `name_mismatch` / `UNVERIFIABLE`), severity, and whether it's a derivable fix or a
human-judgment flag.

Report verdict-first, one line per finding:
`[sev] category  file:line — what's wrong (cited) → fix or flag`.
For derivable drift (dead references, endpoint/type/config tables, a snippet that should
mirror its source) propose a minimal diff. For prose/intent/signature drift, flag for
review — never rewrite it.

Do NOT flag: exempt docs (specs/ADRs/roadmaps/CHANGELOG history/dated snapshots),
`UNVERIFIABLE` claims about other systems, or anything you cannot cite code for.
