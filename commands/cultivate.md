---
description: Cultivate repo hygiene — find what's committed or on disk that shouldn't be: files nothing references, leaked-local files, gitignore gaps, misplaced cross-repo artifacts, the repo's own exposure. Reference-graph first. Proposes untrack/ignore/delete — never auto, never "clean".
---

Run **cultivate** using the **evergreen skill** — the hygiene axis. Hygiene = what's in the repo
that shouldn't be: files nothing references, local-only files leaked into git, gitignore gaps,
cross-repo artifacts in the wrong tree, and the repo's **own exposure** — whether the whole thing is
public when something in it assumes private.

Argument: `{{args}}`. Scope: the whole repo; an optional path narrows it.

**Acceptance bar (non-negotiable).** Before you report, satisfy *every* MUST in
[`skills/evergreen/hard-goals/cultivate.md`](../skills/evergreen/hard-goals/cultivate.md). A run that
fails one — the filesystem isn't inventoried against the index, a `keep` lacks its `git grep`, you
output "clean", or exposure wasn't checked against real `gh` — is **not done**, however thorough it
reads. Those checks are re-runnable by anyone; they are the bar, not your judgment.

## Forbidden shortcuts (take any one and you have NOT run cultivate)

1. **Index-only.** `git ls-files` is often ~10% of a repo. Inventory the filesystem, not the index.
2. **Grep-as-proof.** A filename grep (`AUDIT-*.md`, `.env`…) is a hint, not the test. An empty grep
   is not "clean" — your hints didn't match. Real chaff is named whatever the AI named it.
3. **Skipping the reference graph.** "What's committed that nothing points to" is the primary pass,
   not an afterthought.
4. **Certifying without proof — or with assumed proof.** Evidence is **the command you ran and what
   it returned** — never the index in place of the disk, the prose in place of live state, or recall.
   "Looks intentional" is not a verdict; neither is "the README says it's private." Trust a claim
   instead of checking reality (`gh`, the remote, the filesystem) and you have certified nothing.
   Prove-or-drop runs both ways, and proof is always executed — not read, not recalled, not inferred.

Never output "clean" or "no slop" — output an evidenced inventory plus what you did **not** check.

## Mandatory passes — run all; each leaves its evidence in the output

**A · Filesystem inventory (not the index).**
```sh
git ls-files | wc -l                          # tracked
find . -type f -not -path './.git/*' | wc -l  # on disk
git status --porcelain --untracked-files=all  # untracked-not-ignored (scratch, dumps)
du -sh */ .[^.]*/ 2>/dev/null | sort -rh      # where the bulk lives
```
Separate legitimately-ignored bulk (`target/`, `node_modules/`, `dist/` — cite *why* dismissed) from
suspicious untracked/ignored content (stray `plans/`, committed data, scratch reports).

**B · Reference graph (the spine).** For every committed file that is not an entry point, manifest,
or obviously-imported source, check whether anything reaches it — by path *and* basename:
```sh
git grep -l "<basename>" -- ':!<the file itself>'
```
Zero references → orphan candidate. Reference checks miss lazy/dynamic/aliased imports — confirm
against the code before flagging *source*; don't trust the grep alone.

**C · Pattern hints (a checklist, not the test).** Scan for secrets (`.env*`, `*.pem`, keys), build
artifacts, OS cruft, AI-slop names (`AUDIT-*`, `SUMMARY.md`, `SYNTHESIS.md`, `*-REVIEW*`, stray
`.planning`/`.research`). Found ≠ done; not-found ≠ clean.

**D · Misplaced.** A real file in the wrong tree (an Xcode `.xcprivacy` in a server repo). Right
file, wrong place.

**E · `.evergreen-keep`.** If present (one glob/pattern per line), declared-legit paths are
suppressed. Its presence is evidence — cite it.

**F · Exposure — the repo's own visibility, against reality.** File-level leaks (C) are half of
exposure; the other half is the whole repo. Never assume the visibility — run it:
```sh
gh repo view --json visibility,isPrivate,nameWithOwner 2>/dev/null  # the LIVE state, not a doc's claim
git remote -v                                                       # where a push would land
```
A repo that is **public** when anything in it (a README line, a LICENSE, a config, a deploy note)
assumes private is an exposure finding, the highest severity there is — already irreversible the
moment it was pushed. Cross-check the real `isPrivate` against every documented privacy/visibility
claim; a mismatch is a finding even when no single file is individually wrong. If `gh` is
unavailable, say so and flag visibility as unchecked — never pass it silently.

## Prove every verdict — both directions

"Cited" means you **ran the check and can show what it returned** — the index, the prose, and recall
are not "looking". Every verdict points at an executed command's actual output:
- **Keep** — cite the reference that reaches it, or the owner-intent you *read* (a local
  `.gitignore`/README that says "this dir is public"). Never "seems fine."
- **Surface** — cite zero references **and** the change that orphaned it (the deleted consumer), or
  the leak / misplacement. Read the file and its history before proposing deletion.

## Fix categories

- **Leaked-local** (env, artifacts, local config) → `git rm --cached` + `.gitignore`. File stays on
  disk.
- **Orphan / slop** → propose deletion, approval-gated, never auto, recoverable from history. Source
  believed dead is **flagged, not deleted** — dormant components are often kept on purpose.

## Output

1. Inventory numbers (tracked vs on-disk) and what the bulk is.
2. A table — `file · verdict (keep/untrack/delete/flag) · evidence (executed command + result)`.
3. **Coverage, stated plainly** — what you checked and what you did NOT. Never imply completeness you
   didn't reach.

Nothing runs until the owner approves the batch. Keep internal artifacts (specs, plans, research) in
a gitignored `.planning/` so they never become tracked; the commit-time guard hook backstops it.
