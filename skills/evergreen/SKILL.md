---
name: evergreen
description: Keeps documentation honest with the code it describes. The freshness companion to ponytail — a ride-along reflex that, whenever code changes, asks "does any doc now lie?" and proves the answer against the code before flagging. Use when editing code that has docs, writing/reviewing docs, on "is this doc still right", "doc drift", "stale docs", "keep docs fresh", or before committing changes that touch documented surfaces.
---

# Evergreen

Keep docs true to the code *right now* — not "recently edited". Flag only what you can prove
against the code; an uncited flag is not a finding. A reflex, not a linter: the model does the
checking with the tools it already has (read, grep, diff).

## Persistence

Active every response. When a response changes code that has docs, or writes/reviews docs, surface
a one-line verdict — the finding(s), or `evergreen: docs still match` when the touched surface
holds. Silent only when nothing documented was touched. Off with "stop evergreen" or `/evergreen off`.

## Intensity (`/evergreen off|light|strict`, per repo)

- **light** (default) — ladder rungs 1–3 + cite-only prose checks.
- **strict** — also the full rung-4 semantic pass.
- **off** — paused.

Never blocks a commit — flag, the human decides.

## The freshness ladder

Candidate set = what changed: grep the docs for the touched file paths and edited symbol names, not
the whole tree. Walk the rungs in order; stop at the first that holds *for that claim* (one file can
hold a rung-1 and a rung-3 finding — reporting the first must not suppress the second). Cheap
mechanical checks before semantic reasoning. Cite the code every time.

1. **Vanished path** — an in-repo path a doc names that no longer exists on disk (or was
   renamed/deleted in the diff). Grep, confirm.
2. **Dead contract** — any public surface a doc commits to that no longer exists in code: not just
   CLI flags (`--word`) and env keys (`UPPER_SNAKE`) but whatever the stack exposes (exported
   function, type, method, route, enum case, constant, JSON field; Swift `public`, Go exported,
   Rust `pub`, TS `export`). Code is truth; the doc is the claim. Before calling a contract dead,
   check the diff for a rename/move — `--workers`→`--concurrency` is a reconcile, not a vanished
   contract.
3. **Drifted snippet/signature** — a fenced block, signature, endpoint table, or config schema
   that no longer matches the source. Read both, compare.
4. **Semantic drift** — only now: does the prose still describe current behavior? A precise
   behavioral claim you cannot settle by reading the code (ordering, timing, "returns empty on
   miss") → flag `behavior-asserted — verify manually`; never pass or guess.

If rungs 1–3 are clean, most "stale doc" worry is answered. Spend attention on rung 4.

## Two depths: flag vs winnow

- **flag** (light, every turn) — falsification-biased: report the drift you can cite, move on.
- **winnow** (strict, `/evergreen:winnow`, flourish's verify pass) — affirmative: walk *every*
  documented claim, leave each as **certified** (read doc + current code, they match — cite it),
  **drift** (a finding — cite the code that makes it wrong), or **`unverified — <why>`** (a
  behavioral claim the code can't settle — reported, not passed). Post-winnow silence = every claim
  certified, not "no lie found". `unverified` (this code, can't settle) ≠ `UNVERIFIABLE` (another
  system, dropped).

## Taxonomy

Category: `in_code_not_docs · in_docs_not_code · name_mismatch · UNVERIFIABLE` (another system —
drop, don't guess). Prose/comment rot lenses: `contradiction · stale-reference · signature-mismatch
· outdated-example · resolved-marker · orphaned-comment`. Each finding carries a severity and a
fix-or-flag call.

## Rules

- **Prove it or drop it.** Cite the code that makes the doc wrong, or it isn't a finding. Take an
  adversarial second look ("is this *still* true?") to kill plausible-but-wrong flags.
- **Rot lives in old comments, not new lines.** Read the changed file at HEAD, not just the diff's
  `+` lines. Code moved under a stable doc = live rot (report); a doc wrong the day it was written =
  lower urgency (say which).
- **Editing is not verification.** A touched-for-a-typo doc is not fresh. It clears only when read
  against the current code — both the passage and the code, not "looked plausible".
- **Code is truth, the doc is the claim.** Documented-but-missing = failure; existing-but-undocumented
  = informational. Only a doc that over-promises or contradicts code is a finding.
- **Exempt what leads or freezes.** Specs/ADRs/RFCs/roadmaps/plans lead the code; audit/readiness/
  archive/dated snapshots (ISO names like `AUDIT-2026-05-28`)/CHANGELOG history freeze a point in
  time. Never gate either. Age is not drift.
- **Silence the noise.** Generic symbols (`run`, `build`), cross-repo paths, URL/endpoint strings,
  third-party flags (`git …`, `docker …`), CSS custom properties — not your contracts. Don't re-raise
  a flag rejected this session; honor a repo-local `.evergreen-ignore` (one glob/pattern per line —
  the model reads and applies it; no hook).

## Fix vs flag

- **Propose a diff** (1:1 derivable from code): dead references (renamed/removed path, flag, env
  key), endpoint tables, type/enum/config schemas, a fenced snippet that should mirror its source.
  Minimal change; the human applies it.
- **Flag, never rewrite** (no deterministic anchor): a changed signature, architecture rationale,
  tutorials, "how it works", the security model, the *why*. Point and stop.

## Output

Point at the line; don't scold, pad, or rewrite. One-line read of what changed, one line per
finding, one-line verdict. Exempt docs go on a trailing `left alone:` line, never as a finding.

Per finding: `[high|med|low] category  file:line — what's wrong (cite the code) → fix | flag`

```
evergreen: you renamed `--workers` to `--concurrency`.
  [high] in_docs_not_code  README.md:42 — documents `--workers`; gone from cli.py:30 → fix
  [med]  in_docs_not_code  docs/cli.md:8 — same dead flag → fix
  left alone: docs/adr/0003.md names `--workers` — an ADR, frozen in time.
docs otherwise match the code.
```

Surface still matches → one line: `evergreen: docs still match`.

## The family — truth, craft, hygiene

The reflex is the *truth* axis. Two on-demand commands, same prove-or-drop creed:

- **`/evergreen:flourish <file>` — craft.** The sanctioned prose-rewrite exception (explicit request
  only): restructure an accurate-but-ugly doc toward `skills/evergreen/references/readme-style.md`,
  then run the freshness ladder on the rewrite so nothing ships the code can't back. "Why" derived
  from code by default; `--manual` markers it; a rationale with no code trace is markered, never
  invented.
- **`/evergreen:cultivate` — hygiene.** Repo tidiness: files nothing references, local-only leaks,
  gitignore gaps, misplaced cross-repo artifacts, and the repo's own exposure (public when something
  in it assumes private — checked against `gh`, never the prose). Reference graph first (on disk, not
  the index; an empty grep is not "clean"). Every verdict from **executed** evidence — index, prose,
  and recall are not proof. Proposes untrack/ignore/delete, never auto, never "clean". A commit-time
  guard hook backstops it.

One creed. Truth and craft only flag or propose; hygiene alone may block a commit (a leaked secret
or slop dump is irreversible once pushed), always with an escape hatch. The human keeps the final call.

## When NOT to flag

Exempt docs (specs/ADRs/roadmaps/CHANGELOG history/dated snapshots). Intent/rationale prose (the
*why*). Claims about external systems (`UNVERIFIABLE`). Anything you cannot cite code for. Old but
still-true docs (age is not drift).
