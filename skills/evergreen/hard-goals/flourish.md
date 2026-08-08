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

7. **MUST reach the quick start within 400 words.**
   CHECK:
   ```sh
   awk '/^#{2,3} *(Install|Quick ?start|Getting started|Usage)/{exit} {c+=NF} END{print c}' RESULT.md
   ```
   Pass = the printed number is ≤ 400, or the doc has no install path at all (a pure explainer —
   say which in the report). The bound is derived, not invented: `readme-style.md` caps the whole
   gateway archetype at 450 words, so a doc that hasn't reached install by 400 cannot be one.
   A visitor who must read an essay before learning how to try the thing will not try the thing.

8. **MUST keep the open page inside the length band.**
   CHECK:
   ```sh
   python3 - <<'EOF'
   import re,sys
   t=open('RESULT.md').read()
   open_page=re.sub(r'<details>.*?</details>','',t,flags=re.S)
   print(len(open_page.split()))
   EOF
   ```
   Pass = ≤ 2,500 words, `readme-style.md`'s own onboarding ceiling. `<details>` content is
   rent-free and excluded, per `readme-style.md`'s "the budgets measure the open page, not the
   collapsed reference beneath it." Over the bound, the fix is goals 1–3's demotion path —
   `<details>` or a linked file — never deletion.

9. **MUST run the repository's own checks before reporting the rewrite as done.**
   CHECK: the report shows the verification command it ran and that command's verdict line — in
   this repository, `bash tests/all.sh` and its `GREEN` / `NOT GREEN` line. Pass = command and
   verdict both shown, or the report states that the repository declares no verification entry
   point. A partial run does not count: naming one suite when the repository runs four is a fail.
   Never substitute a grep over the output for the verdict line; suites disagree on whether failure
   prints `FAIL` or `not ok`, and a pattern matching one silently passes the other.

**Goal 9 exists because documentation is load-bearing.** A doc is not inert text a rewrite can
reshape freely: prose gets asserted on. Repositories bind doc content to code — a policy block that
must appear identically across surfaces, a platform bound a test greps for, an exit-code sentence
that must appear exactly twice. On 2026-08-08 a flourish run on this repository passed every craft
floor, every conservation check, and the entire Python test suite, then broke 36 assertions in a
shell suite no Python runner collects. Structure, voice, and truth were all fine. The rewrite was
still wrong, and nothing in goals 1–8 could see it.

**Goals 7 and 8 exist because the structure floor was a judgment and judgments drift.** On
2026-08-08 this repo's own README passed every floor at 4,571 words with the install instruction at
word 2,031, behind a machine-readable policy blob and three separate repetitions of a benchmark
caveat. Every line of it was true, conserved, voiced, and correctly structured *in order* — the
spine was right and the document was still unusable to a stranger. No goal above 6 could catch that,
because none of them counted anything.

## Why this works without a second AI at runtime

Every CHECK is a grep or a count that anyone — a human, or the same model on a later pass — re-runs
to the same answer. The frozen contract *is* the external arbiter. A run that ships a gorgeous hero
over a gutted body fails goals 1–3 mechanically, full stop — which is exactly the run that sailed
through before this file existed.
