# Hard goals — `winnow`

**Frozen contract.** A winnow run that fails any goal below is *not done*, no matter how good its
prose reads. These are pre-committed: the bar is fixed before the work, so "done" can't be redefined
afterward to match whatever got produced.

## What makes a goal "hard" (the test for any goal added here)

1. **Binary** — pass or fail. No "looks good", "should work", "mostly".
2. **Checkable without trusting the AI** — proven by an executed command, a `file:line`, or a count
   that a third party (or the same AI on a later run) re-runs and gets the identical yes/no.
3. **Pre-committed** — written before the work starts.
4. **Covers the hard part** — doing the easy 80% and skipping the painful 20% must *fail* the bar.

If a goal's check needs the AI's opinion to pass, it isn't hard — rewrite it until it doesn't.

## The goals

1. **MUST enumerate the whole claim set before judging any of it.**
   CHECK: the run states `N` = documented claims in scope **and shows the command** that produced N
   (a grep of the claim-bearing lines in the changed docs). Pass = N is shown with its command.

2. **MUST give each of the N claims its own verdict** — `certified` / `drift` / `unverified`. No
   claim folded into a summary.
   CHECK: number of verdict rows == N. Pass = every claim has a row; nothing says "the rest are fine".

3. **MUST cite a code `file:line` on every `certified` row** — the code that makes the claim true,
   not the doc.
   CHECK: grep the certified rows for a code `path:line`. Pass = zero certified rows without one.

4. **MUST prove every `drift` row** — the path/flag/symbol it calls gone is actually gone from the code.
   CHECK: re-run the grep for each cited thing against the code → empty. Pass = all empty (nothing it
   called dead is actually present).

5. **MUST surface every claim it could not settle** as `unverified — <why>`, never silently pass or
   drop it.
   CHECK: `certified + drift + unverified == N`. Pass = the three counts sum to N.

6. **MUST keep exempt docs (specs / ADRs / roadmaps / dated snapshots) out of findings** — listed
   only under a trailing `left alone:` note.
   CHECK: no exempt-doc path appears on a `certified` or `drift` row. Pass = zero.

## Why this works without a second AI at runtime

Every CHECK is a grep or a count that anyone — a human, or the same model on a later pass — re-runs to
the same answer. The frozen contract *is* the external arbiter; no live cross-model call is required.
A run that reads great but fails goal 4 is a failed run, full stop.
