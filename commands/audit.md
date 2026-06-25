---
description: Audit documentation freshness against the code — deterministic scan first, then triage findings.
---

Run an evergreen documentation-freshness audit. Follow the freshness ladder from
the evergreen skill — cheapest signal first, prove-or-drop, never gate exempt docs.

1. Run the deterministic engine and read its findings:
   `bash "${CLAUDE_PLUGIN_ROOT}/bin/evergreen-scan" --base ${1:-origin/main}`
2. For each `in_docs_not_code` / `name_mismatch` finding, confirm it against the
   code (cite the file/line that makes the doc wrong). Drop anything you can't cite.
3. Add any contract-level drift the engine can't see (rung 2): a changed public
   signature, route, env var, or config key whose doc still shows the old shape.
   Code is the source of truth; the doc is the claim under test.
4. Classify each surviving finding: category (`in_code_not_docs` / `in_docs_not_code`
   / `name_mismatch` / `UNVERIFIABLE`), severity, and **Auto-Fixable?**
5. Report verdict-first, one line per finding:
   `[sev] category  file:line — what's wrong (cited) → fix or flag`.
   For auto-fixable items (signatures, paths, endpoint/type/config tables) offer a
   diff. For prose/intent drift, flag for review — never rewrite it.

Do NOT flag: exempt docs (specs/ADRs/roadmaps/CHANGELOG history), `UNVERIFIABLE`
claims about other systems, or anything you cannot cite code for.
