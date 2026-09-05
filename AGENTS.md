# Evergreen — keep the docs honest

Keep docs true to the code right now; flag only what you can prove against the code.

**Active every response.** When you change code that has docs, or write or review docs, give a one-line verdict: the finding(s), or `evergreen: docs still match`. Off on "stop evergreen".

## Shared contracts live once
`skills/evergreen/DIGEST.md` is the binding text for four contracts; this file routes to it rather than copying it, because a second copy is drift. Read the matching section before you: mutate anything external or report pushed, merged, clean, complete, released, lost, erased, or not run (receipt policy); touch a version, build number, badge, or release stream (release identity); run a repository test command (safe prove by test); accept provider evidence or a source map (provider boundary: candidates, never findings or verdicts). Stage and commit in separate tool calls.

## Freshness ladder
Candidate set is the diff, not the whole tree. In order, the first rung that holds for a claim wins; cite the code: 1. vanished path; 2. dead contract (a documented flag, env key, export, route, or field gone from code); 3. drifted snippet or signature; 4. semantic drift, where a claim code cannot settle is `behavior-asserted — verify manually`, never passed or guessed.

## Intensity
Per repo `off | light | strict`, default light. Rung: `path · contract · snippet · prose`. light must never emit a `prose` finding; an undeclared mode is a violation. A deep pass certifies every claim as certified, drift, or `unverified — <why>`.

## Rules
- Prove it or drop it: "confirmed fresh" means you read the doc and the current code.
- Code is truth, the doc is the claim. Documented-but-missing is a failure; existing-but-undocumented is informational.
- Exempt what leads or freezes (specs, plans, snapshots, CHANGELOG). Age is not drift.
- Silence the noise: third-party flags, CSS variables, URLs, cross-repo paths, generic symbols; honor `.evergreen-ignore`.
- Propose a diff only for what is 1:1 derivable from code; flag, never rewrite, what has no deterministic anchor.

## Output
`[high|med|low] category rung  file:line — what's wrong (cite the code) → fix | flag`; categories `in_code_not_docs · in_docs_not_code · name_mismatch · release_identity_drift · UNVERIFIABLE` (drop the last), plus `unverified` in a deep pass.

## Working in this repository
`bash tests/all.sh` is the definition of green; run it before calling a change done. `tests/hooks.sh` checks that README.md, DESIGN.md, SKILL.md, and DIGEST.md carry the receipt policy sentence for sentence and that this file routes to DIGEST.md. Policy text never leaves README.md.
CI treats repository files, diffs, and comments as untrusted data; complete and clean means zero drift and zero unverified; inconclusive fails unless `fail_on_inconclusive: false`.
Parallel-first: one file boundary and worktree per agent, reconcile at declared gates, serialize only true shared state.
The hygiene guard inspects the finalized staged index on commit-only calls; an index-writing subcommand paired with a commit in one call is refused; `EVERGREEN_GUARD=off` is the explicit bypass. `bin/evergreen judge` is the second provider for winnow, seed, flourish, and cultivate; the release gate refuses a record whose judge block is missing, abstained, or reports drift.

## Absolute taboos
- Compression lie: when a file is the source of record, every step reads it, never a summary; if verbatim execution is impossible or wrong, stop and say so before spending anything.
- Assert-then-verify: no state is called fact before the check runs, evidence ships in the same message, and what cannot be verified now is labelled unverified.

## Docs
`skills/evergreen/DIGEST.md` (binding contracts); `skills/evergreen/SKILL.md` (full skill); `README.md`, `docs/DESIGN.md` (same policy text).
