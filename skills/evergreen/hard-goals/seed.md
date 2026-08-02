# Hard goals — `seed`

**Frozen contract.** A seed run that fails any goal below is *not done*, no matter how readable the
docs it wrote. These are pre-committed: the bar is fixed before the work, so "done" can't be
redefined afterward to match whatever got produced. The failure mode this contract exists to stop is
the **confident fabrication** — generated docs that read authoritative while asserting what the code
never backed.

## What makes a goal "hard" (the test for any goal added here)

1. **Binary** — pass or fail. No "looks good", "should work", "mostly".
2. **Checkable without trusting the AI** — proven by an executed command, a `file:line`, or a count
   that a third party (or the same AI on a later run) re-runs and gets the identical yes/no.
3. **Pre-committed** — written before the work starts.
4. **Covers the hard part** — doing the easy 80% and skipping the painful 20% must *fail* the bar.

If a goal's check needs the AI's opinion to pass, it isn't hard — rewrite it until it doesn't.

## The goals

1. **MUST enumerate the candidate set from the impact provider, not taste.**
   CHECK: the run states `N` = ranked candidates in scope **and shows the executed command** that
   produced N (`python3 <plugin-root>/bin/evergreen impact --json <scope>`). Pass = N is shown with
   its command and output.

2. **MUST cross-reference every candidate against the existing doc set.**
   CHECK: gap rows == N — each candidate has a row with its executed `git grep` over the doc set and
   the verdict `documented` / `undocumented`. Pass = every candidate has a row; nothing says "the
   rest are covered".

3. **MUST ledger every prose claim in the seeded docs with a code `file:line`.**
   CHECK: the report contains a claim ledger; every row cites a code `path:line` (the code that
   makes the sentence true, not the doc). Pass = zero ledger rows without one.

4. **MUST winnow the seeded output before proposing it.**
   CHECK: the report shows winnow verdict counts for the seeded docs — `certified` == claim-ledger
   rows, `drift` == 0, `unverified` == 0 (`seed:gap` slots are exempt as marked). Pass = counts
   shown and they hold.

5. **MUST mark every sentence the code can't settle — zero unmarked speculation.**
   CHECK: every declarative sentence in the seeded docs either has a claim-ledger row (goal 3) or
   carries the `seed:gap` marker (grep `seed:gap`). Pass = zero sentences with neither.

6. **MUST be purely additive.**
   CHECK: the proposed diff contains zero deleted lines in any pre-existing doc file (`git diff`
   shows no `-` lines outside new-file headers). Pass = zero deletions.

7. **MUST account for the whole candidate set.**
   CHECK: `written + informational-listed == N`. Pass = the two counts sum to N; the long tail below
   the cut is listed, never silently dropped.

## Why this works without a second AI at runtime

Every CHECK is a grep or a count that anyone — a human, or the same model on a later pass — re-runs
to the same answer. The frozen contract *is* the external arbiter. A run that writes beautiful docs
but fails goal 4 shipped unproven claims — a failed run, full stop, because seeded docs are claims
from the moment they exist.
