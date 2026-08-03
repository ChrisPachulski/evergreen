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

1. **MUST start from a complete, surface-shaped inventory.**
   CHECK: the report states source files in scope `S`, provider-scanned files `S`, and candidates
   `N`; every candidate has `symbol`, `kind`, declaration code `path:line`, and rank; no warning
   contains `truncated`; every provider warning containing `outside inventory` is quoted in the
   report as a named exclusion (the files it counts are out of surface, never classified). Pass =
   counts match, every row has the required fields, and every `outside inventory` warning is
   quoted. Path-only or incomplete output is not done.

2. **MUST triage the ranking without cherry-picking, stopping only when it goes cold.**
   CHECK: let `R` be the last inspected rank. Gap rows == `R`, in provider order, with no skipped
   rank; the walk stops only at rank `N` or after 10 consecutive `documented`/`not worthy`
   verdicts, and the stopping streak is visible in the table's last 10 rows. Candidates after `R`
   are `deferred — ranking cold at rank R`, never falsely classified as documented or unworthy.

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

7. **MUST put the write count in the owner's hands.**
   CHECK: the report shows the full worthy list with a stated recommendation BEFORE any doc is
   drafted, then the owner's chosen count `C`; `documented + worthy + not-worthy + deferred == N`;
   `written ≤ C ≤ worthy`; with no owner answer, `written == 0`; zero pre-diff documented
   candidates are written. Pass = all counts hold and the list-then-choice ordering is visible in
   the report.

8. **MUST seed useful, small, repeat-visible documentation.**
   CHECK: every written candidate has at least one `reader-use` ledger row citing code outside its
   declaration/signature; `git add -N` every newly created doc file first (untracked files produce
   no `git diff --numstat` row, so an unregistered new doc would evade the bounds entirely), then
   `git diff --numstat` shows added lines `A ≤ 60 × written`; changed doc paths and
   `seed:gap` markers are each `≤ written`; post-diff fixed-string grep over `D` **plus every
   newly created doc path** returns a non-marker hit for every written symbol. Pass = all checks
   hold; `written == 0` implies no documentation diff.

## Why this works without a second AI at runtime

Every CHECK is a grep or a count that anyone — a human, or the same model on a later pass — re-runs
to the same answer. The frozen contract *is* the external arbiter. A run that writes beautiful docs
but fails goal 4 shipped unproven claims — a failed run, full stop, because seeded docs are claims
from the moment they exist.
