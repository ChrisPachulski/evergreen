---
description: Cultivate repo hygiene — find what's committed or on disk that shouldn't be: files nothing references, leaked-local files, gitignore gaps, misplaced cross-repo artifacts. Reference-graph first. Proposes untrack/ignore/delete — never auto, never "clean".
---

Run **cultivate** using the **evergreen skill** — the hygiene axis. Truth is docs-vs-code; craft
is ugly-vs-good; hygiene is **what's in the repo that shouldn't be**: files nothing references,
local-only files leaked into git, gitignore gaps, and cross-repo artifacts in the wrong tree.

Argument given: `{{args}}`. Scope: the whole repo; an optional path narrows it.

## The four shortcuts that make cultivate worthless — all forbidden

These are the exact ways a lazy pass fakes a hygiene check. Take any one and you have **not** run
cultivate:

1. **Index-only.** `git ls-files` is often ~10% of a repo. Chaff lives on disk too — untracked
   planning dumps, scratch, data. You MUST inventory the filesystem, not just the index.
2. **Grep-as-proof.** A narrow filename grep (`AUDIT-*.md`, `.env`…) is a *hint*, never the test.
   An empty grep is **not** "clean" — it means your hints didn't match. Real chaff is named
   whatever the AI named it (`iter_3_output.txt`, a stray `.xcprivacy`).
3. **Skipping the reference graph.** "What's committed that nothing points to" is the ONLY pass
   that finds real chaff. It is **mandatory and primary**, not a low-confidence afterthought.
4. **Certifying without proof.** You may not call a file *legit* any more than *chaff* without
   citing evidence. "Looks intentional" is not a verdict. **Prove-or-drop runs both ways.**

And you never output "clean" or "no slop" — you output an evidenced inventory plus what you did
**not** check.

## Mandatory passes — run all; each must leave its evidence in the output

**A · Filesystem inventory (not the index).** Report the real numbers and look at what they hide:
```sh
git ls-files | wc -l                          # tracked
find . -type f -not -path './.git/*' | wc -l  # on disk
git status --porcelain --untracked-files=all  # untracked-not-ignored (scratch, dumps)
du -sh */ .[^.]*/ 2>/dev/null | sort -rh       # where the bulk lives
```
Separate **legitimately-ignored bulk** (`target/`, `node_modules/`, `dist/` — build output, not
chaff; cite *why* you dismiss it) from **suspicious untracked/ignored content** (a stray `plans/`
dir, committed data, scratch reports).

**B · Reference graph — the spine, mandatory.** For every committed file that is not an entry
point, manifest, or obviously-imported source, ask whether anything reaches it — by path *and*
basename:
```sh
git grep -l "<basename>" -- ':!<the file itself>'
```
Zero references → **orphan candidate**. This is where a dead asset (a `gem.webm` whose component
was deleted), a stray dump (`iter_*.txt`), and dead source (an unimported component) surface.
Reference checks miss lazy/dynamic/aliased imports — so before flagging *source*, confirm against
the code; don't trust the grep alone.

**C · Pattern hints — a checklist, not the test.** Also scan for secrets (`.env*`, `*.pem`, keys),
build artifacts, OS cruft, and AI-slop names (`AUDIT-*`, `SUMMARY.md`, `SYNTHESIS.md`, `*-REVIEW*`,
stray `.planning`/`.research` committed into the tree). Found ≠ done; not-found ≠ clean.

**D · Misplaced.** A real file in the wrong tree — an Xcode `.xcprivacy` in a server repo, a server
config in a mobile repo. Right file, wrong place.

**E · `.evergreen-keep`.** If present (one glob/pattern per line), declared-legit paths are
suppressed. Its presence is evidence — cite it.

## Prove every verdict — both directions

"Cited" means you actually looked:
- **Keep** — cite the reference that reaches it, or the owner-intent you *read* (a local
  `.gitignore`/README that says "this dir is public"). Never "seems fine."
- **Surface** — cite zero references **and** the change that orphaned it (the deleted consumer),
  or the leak / misplacement. Look before you delete: read the file and its history first.

## Fix categories

- **Leaked-local** (env, artifacts, local config) → `git rm --cached` + `.gitignore`. The file
  stays on disk.
- **Orphan / genuine slop** → propose **deletion**, approval-gated, never auto, recoverable from
  history. Source you believe dead is **flagged, not deleted** — dormant components are often kept
  on purpose; that's the owner's call.

## Output

1. Inventory numbers (tracked vs on-disk) and what the bulk is.
2. A table — `file · verdict (keep/untrack/delete/flag) · evidence (refs found, or zero-refs + cause)`.
3. **Coverage, stated plainly** — what you checked and what you did NOT (e.g. "reference-checked
   every committed non-source asset and doc; did not deep-verify each `.ts` import"). Never imply
   completeness you didn't reach.

Nothing runs until the owner approves the batch. See `examples/cultivate-orphan.md` for a worked
reference-graph catch.

> Prevention beats cleanup: keep internal artifacts (specs, plans, research) in gitignored
> `.planning/` so they never become tracked. The commit-time guard hook is the backstop.
