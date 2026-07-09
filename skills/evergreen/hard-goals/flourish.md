# Hard goals — `flourish`

**Frozen contract.** A flourish run that fails any goal below is *not done*, no matter how good the
hero looks. These are pre-committed: the bar is fixed before the work, so "done" can't be redefined
afterward to match whatever got produced. The failure mode this contract exists to stop is the
**beautiful gutting** — a rewrite that passes face and voice while quietly deleting the only copy
of true reference material.

## What makes a goal "hard" (the test for any goal added here)

1. **Binary** — pass or fail. No "looks good", "should work", "mostly".
2. **Checkable without trusting the AI** — proven by an executed command, a `file:line`, or a count
   that a third party (or the same AI on a later run) re-runs and gets the identical yes/no.
3. **Pre-committed** — written before the work starts.
4. **Covers the hard part** — doing the easy 80% and skipping the painful 20% must *fail* the bar.

If a goal's check needs the AI's opinion to pass, it isn't hard — rewrite it until it doesn't.

## The goals

1. **MUST conserve every source section.**
   CHECK: extract the `##`/`###` heading set of the source doc (grep, shown in the run); every
   heading is present in the result, present in a file the result links to, or listed on the cut
   ledger (goal 2). Pass = heading-set diff minus the ledger is empty. A renamed section counts
   only if the run names the old→new mapping.

2. **MUST name every cut.**
   CHECK: the run's report contains a conservation ledger with one row per removed section —
   section name, reason, and where the content now lives. Pass = every heading missing from the
   result appears on the ledger; nothing is covered by "trimmed for length".

3. **MUST NOT delete sole-copy reference material.**
   CHECK: for each ledger row, the "now lives" column names a real destination (a linked file that
   exists on disk, a docs URL, a `<details>` block in the result) — or the row is marked
   `sole copy — held for approval` and the content is NOT removed from the written file. Pass =
   zero ledger rows that remove content while naming no surviving home.

4. **MUST keep the face.**
   CHECK: the result's first line is a centered hero (`<h1 align="center">` or a centered logo
   block) and a tagline `<em>` line follows it. Pass = both greps hit.

5. **MUST verify the rewrite's claims** (the truth trial is not optional garnish).
   CHECK: the report shows certified / cut / markered counts for the rewrite's factual claims, and
   every new badge and feature bullet is covered by one of the three. Pass = counts shown and sum
   to the claim set.

6. **MUST show the rewrite** — a diff or the full result plus the ledger, in the report, before or
   at write time. Silent overwrite fails.
   CHECK: the report contains the diff (or full text) and the ledger. Pass = both present.

## Why this works without a second AI at runtime

Every CHECK is a grep or a count that anyone — a human, or the same model on a later pass — re-runs
to the same answer. The frozen contract *is* the external arbiter. A run that ships a gorgeous hero
over a gutted body fails goals 1–3 mechanically, full stop — which is exactly the run that sailed
through before this file existed.
