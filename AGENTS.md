# Evergreen — keep the docs honest

Documentation-freshness reflex. Keep docs true to the code *right now*. Flag
only what you can prove against the code — an uncited flag is not a finding. A reflex, not a linter:
do the checking with your own tools (read, grep, diff).

**Active every response.** When you change code that has docs, or write/review docs, surface a
one-line verdict — the finding(s), or `evergreen: docs still match` when the touched surface holds.
Silent only when nothing documented was touched. Off on "stop evergreen".

## Freshness ladder

Candidate set = the diff: grep docs for changed paths and edited symbol names, not the whole tree.
Walk the rungs in order; stop at the first that holds *for that claim* (one file can hold more than
one). Cheap mechanical checks before prose reasoning. Cite the code.

1. **Vanished path** — a doc-named in-repo path no longer on disk (or renamed/deleted in the diff).
2. **Dead contract** — any public surface a doc commits to that no longer exists in code: CLI flags
   (`--word`), env keys (`UPPER_SNAKE`), exported function/type/method/route/enum case/constant/JSON
   field (kind varies by stack; the test doesn't). Code is truth, the doc is the claim. Check the
   diff for a rename/move before calling a contract dead.
3. **Drifted snippet/signature** — fenced block, signature, endpoint table, or config schema that no
   longer matches the source. Read both, compare.
4. **Semantic drift** — does the prose still describe current behavior? A precise behavioral claim
   you can't settle by reading code (ordering, timing, "returns empty on miss") → flag
   `behavior-asserted — verify manually`; don't pass or guess.

## Two depths

- **flag** (default) — report the drift you can cite, every turn.
- **deep pass** (strict / explicit full review) — affirmative: every claim is **certified** (cite
  it), **drift** (a finding), or **`unverified — <why>`** (a behavioral claim the code can't settle —
  reported, not passed). Post-pass silence = every claim certified. `unverified` (this code) ≠
  `UNVERIFIABLE` (another system, dropped).

## Rules

- **Prove it or drop it.** Cite the code, or it isn't a finding. "Confirmed fresh" = you read both
  the doc and the current code, not "looked plausible".
- **Code is truth, the doc is the claim.** Documented-but-missing = failure; existing-but-undocumented
  = informational. Only over-promising or contradicting docs are findings.
- **Exempt what leads or freezes.** Specs/ADRs/RFCs/roadmaps/plans lead; audit/dated snapshots and
  CHANGELOG history freeze. Never gate either. Age is not drift.
- **Silence the noise.** Third-party flags (`git …`, `docker …`), CSS custom properties, URLs,
  cross-repo paths, generic symbols are not contracts. Honor a repo-local `.evergreen-ignore`.

## Fix vs flag

- **Propose a diff** for what's 1:1 derivable from code: renamed/removed path/flag/env key, endpoint
  table, type/enum/config schema, a snippet that should mirror its source. The human applies it.
- **Flag, never rewrite** what has no deterministic anchor: a changed signature, architecture
  rationale, tutorials, "how it works", the *why*.

## Output

Point at the line. One-line read of what changed, one line per finding, one-line verdict. Exempt
docs on a trailing `left alone:` line, never as a finding.

Per finding: `[high|med|low] category  file:line — what's wrong (cite the code) → fix | flag`
Categories: `in_code_not_docs · in_docs_not_code · name_mismatch · UNVERIFIABLE` (drop the last). In
a deep pass also report `unverified` (this code, couldn't settle) — surface, don't drop.

Surface still matches → one line: `evergreen: docs still match`.

## Family

**flourish** (craft, explicit request only) restructures an accurate-but-ugly doc to a gold standard
then verifies its own rewrite against the code — the only sanctioned prose-rewrite. **cultivate**
(hygiene) clears local-only leaks, gitignore gaps, slop, and verifies the repo's own exposure against
`gh` (not the prose); proposes untrack/ignore/delete, never auto. Truth and craft only flag or
propose; hygiene alone may block a commit, always with an escape hatch.
