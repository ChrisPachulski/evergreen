---
description: Audit documentation freshness against the code — deterministic scan first, then triage findings.
---

Run an evergreen documentation-freshness audit. Follow the freshness ladder from
the evergreen skill — cheapest signal first, prove-or-drop, never gate exempt docs.

1. Run the deterministic engine and read its findings (add `--score` for a freshness
   read):
   `bash "${CLAUDE_PLUGIN_ROOT}/bin/evergreen-scan" --base ${1:-origin/main} --score`
2. For each `in_docs_not_code` / `needs_reverify` finding, confirm it against the
   code (cite the file/line that makes the doc wrong, or — for `needs_reverify` — read
   the moved source and decide if the doc still holds). Drop anything you can't cite.
3. The engine already catches doc-documented CLI flags and env/config keys that no
   code contains, embed blocks that drifted from their source, and pinned manifest
   sources that moved. Add only the drift it still can't see: a public signature, type,
   or route that *exists but changed shape* (the doc shows the old parameters/return/
   path), and semantic/prose drift (rung 4). Code is the source of truth; the doc is
   the claim under test.
4. Classify each surviving finding: category (`in_code_not_docs` / `in_docs_not_code`
   / `name_mismatch` / `needs_reverify` / `UNVERIFIABLE`), severity, and **Auto-Fixable?**
5. Report verdict-first, one line per finding:
   `[sev] category  file:line — what's wrong (cited) → fix or flag`.
   For the *mechanically* derivable subset the engine fixes itself — embed refresh,
   manifest re-pin — run `evergreen-scan --fix`. For other auto-fixable items
   (signatures, paths, endpoint/type/config tables) offer a diff. For prose/intent
   drift, flag for review — never rewrite it.

Do NOT flag: exempt docs (specs/ADRs/roadmaps/CHANGELOG history), `UNVERIFIABLE`
claims about other systems, or anything you cannot cite code for.
