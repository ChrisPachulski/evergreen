---
description: Cultivate repo hygiene — local-only files leaking into git, gitignore gaps, and AI-slop that has no business being tracked or public. Walks a hygiene ladder, cites why, proposes untrack/ignore/delete — never auto.
---

Run **cultivate** using the **evergreen skill** — the hygiene axis. Distinct from truth (docs vs
code) and craft (ugly vs gold-standard): this is repo **tidiness**. Local-only files leak into
version control, `.gitignore` misses what it should catch, and AI floods repos with miscellaneous
`.md`/`.html`/whatever that has no business being tracked or public. File-type agnostic.

Argument given: `{{args}}`. Default scope: the whole repo. An optional path narrows it.

**Same creed as the rest of evergreen: prove it or drop it.** Every flagged file cites *which
rung and why* — the ignore-rule it violates, the junk pattern it matches, or the missing
reference. No "this looks like clutter." And cultivate **proposes — it never auto-deletes or
auto-commits.** You approve every change.

## The hygiene ladder (cheap/mechanical first, judgment last)

1. **Gitignore correctness** *(mechanical, highest precision)* — tracked files that match an
   existing ignore pattern (they slipped in before the rule); standard stack ignores that are
   missing (`node_modules`, `__pycache__`, `target/`, `dist/`, `.env`, `.DS_Store`). → propose fix.
2. **Known-junk patterns** *(cheap, high precision)* — secrets (`.env`, `*.pem`, key files),
   build artifacts, caches, OS cruft (`.DS_Store`, `Thumbs.db`), and **AI-slop signatures**:
   `AUDIT-*.md`, `SUMMARY.md`, `SYNTHESIS.md`, `*-REVIEW*.md`, scratch reports, stray
   `.planning`/`.research` dumps committed into the tree. → propose fix.
3. **Unreferenced = suspect** *(heuristic, lower confidence)* — build a reference graph: a file
   nothing links to, imports, or mentions (no README link, no code import, no doc reference). →
   **flag for review only**, no destructive proposal.
4. **`.evergreen-keep` allowlist** *(optional override)* — if a repo-local `.evergreen-keep`
   exists (one glob/pattern per line), declared-legit files are suppressed from rungs 2–3. Absent
   ⇒ rungs run on patterns/refs alone; nothing over-flagged. (Mirror of `.evergreen-ignore`.)

Confidence sets the action: rungs 1–2 propose a fix; rung 3 flags for review.

## Two fix categories

- **Should only be local** (env, artifacts, local config) → `git rm --cached <file>` + add to
  `.gitignore`. **The file stays on disk** — it's untracked, not deleted.
- **No business existing at all** (genuine slop) → propose **deletion**, approval-gated, never
  auto. Read the file first — don't propose nuking something whose content contradicts how it
  reads as slop (the safety rule applies: look before you delete).

## Output

Verdict-first, one line per finding:
`[rung] file — why it's flagged (cited) → untrack | ignore | delete | review`.
Then emit the concrete plan: the `.gitignore` additions, the `git rm --cached` set, and the
flagged deletions — for your approval as a batch. Nothing runs until you say so.

> Prevention beats cleanup: cultivate maintains the repo's gitignore coverage so internal
> artifacts (specs, plans, research) land in the gitignored `.planning/` by default and never
> become tracked in the first place. The commit-time guard hook is the backstop.
