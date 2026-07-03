# GOAL: extend the trial verdict-harness from winnow to cultivate and flourish

**For:** a future session picking this up cold.
**Status:** done — all three phases executed 2026-07-02 (see definition of done). **Written:** 2026-07-02.
**Depends on:** the trial judge already built for winnow (`eval/bench/run_bench.py:judge`, and the
"Put the judgment on trial" section of `skills/evergreen/SKILL.md`).

## The idea in one paragraph

The trial we built for **winnow** is not a winnow-specific thing — it's a reusable **verdict
harness** for any call that (a) costs something real when it's wrong and (b) is a *judgment*, not a
grep fact. The shape is fixed: **snap** (first-instinct verdict, stated + logged, a weighted vote) →
**challenge it must survive** (argue the snap is wrong, in whichever direction it went; a "looks
fine" gets no free pass) → **three independent blind reads** (they never see the snap, the
challenge, or their model tier, so a "confirming" read can't rubber-stamp) → **blind-spot** (surface
an angle everyone missed; raises, never decides) → **synthesis** (weigh it all, only when the
evidence isn't unanimous; no separate immune rule — that needed iterations a single-shot judgment
doesn't have). Model tiers: strong (Opus) on the snap and the contested synthesis; cheap (Sonnet) on
the challenge, prongs, and blind-spot. **Fable is banned from every role.** This goal points that
harness at the other two evergreen commands.

## The bar to clear

Cultivate and flourish make **higher-stakes** calls than winnow — cultivate can *delete a file* or
*block a commit* (irreversible once a secret is pushed); flourish *rewrites prose*. Both currently
rest on a snap "this looks like slop / this looks done." The bar: no destructive or ship-a-rewrite
verdict emits until it has survived the trial, and the trial is a **shared discipline** all three
commands invoke, not three copies.

## Phase 1 — Hoist the trial into a shared skill discipline (do this first)

The commands are prompt-driven, so the deliverable is skill-level, not a code refactor. Do **not**
abstract the trial into shared code prematurely (`run_bench.py:judge` stays the reference
implementation for the *benchmark*; the product commands are prose).

1. In `skills/evergreen/SKILL.md`, generalize "Put the judgment on trial" so it reads as a reusable
   harness parameterized by three things a caller supplies: the **claim**, the **verdict space**,
   and the **prong prompts**. State the invariant once: mechanical facts (a grep count, a `gh`
   exposure check) skip the trial; only *interpretations* of those facts go on trial.
2. Have `commands/winnow.md`, `commands/cultivate.md`, `commands/flourish.md` each *point at* that
   section and supply only their command-specific snap question + prong set.

## Phase 2 — Cultivate (highest value: it deletes and blocks)

Cultivate is the best fit for the trial in the whole tool, because its verdicts are the destructive
ones. Map the harness onto its **judgment-call** findings only:

- **snap:** "this file is unreferenced slop" / "this is a secret that shouldn't be tracked" /
  "this repo is public when something in it assumes private."
- **challenge (must survive):** "no — here's where it's reached (a dynamic import, a config glob, a
  CI path, a build step), or here's why it's a legitimate fixture." A "delete this" that can't
  survive that does not emit.
- **three blind reads:** defend the file's right to exist / prove it is truly orphaned / hardest
  case it is genuinely dangerous to keep.
- **blind-spot (the money one for cultivate):** "did all three miss that this file *is* reached,
  just not by a static grep?" — the exact failure that makes a hygiene tool delete something
  load-bearing.

Rules for this phase:
- The **mechanical evidence stays out of the trial** — "zero inbound references" and "gh says
  public" are facts; only the conclusion drawn from them ("therefore safe to delete / therefore a
  real leak") is tried.
- **Keep the human override on the blocking path regardless.** The trial lowers false blocks; it
  does not earn the right to remove the escape hatch. A blocked commit is high-cost when wrong.
- Cultivate produces few judgment-call findings per run, so the multi-call cost stays bounded.

## Phase 3 — Flourish (two different judgments → two trials)

1. **Truth pass — reuse the winnow trial verbatim.** Every claim the beautified rewrite makes goes
   on trial against the code before the rewrite ships. No adaptation; it *is* winnow.
2. **Craft pass — an adapted trial that hardens the face-gate.** This is the fix for the
   already-observed bug (a structurally-correct-but-voiceless doc that sailed through). Turn the
   face scorecard from a checklist into survive-the-attack:
   - **snap:** "this rewrite is gold-standard, ship it."
   - **challenge (must survive):** "no — it's still flat: here's the definitional opener with no
     hook, here's the missing centered hero, here's the toneless section intro."
   - **three blind reads:** defend it's done / attack the prose (voice, hook) / attack the structure
     (hero, spine, badges, earned visual).
   - **blind-spot:** "did everyone miss that a badge or feature bullet claims something the code
     can't back?" (routes back to the truth pass).
   - A "looks done" that can't beat "it's still ugly" does not ship.

## Definition of done

- [x] A single shared "Put the verdict on trial" discipline in `SKILL.md`, parameterized by claim /
      verdict space / prong prompts, that winnow, cultivate, and flourish all point at.
- [x] `cultivate.md` runs its judgment-call findings (slop / leak / exposure interpretation) through
      the trial; mechanical facts (reference counts, `gh` exposure) stay out; the blocking path
      keeps a human override.
- [x] `flourish.md` runs the truth pass through the winnow trial and the craft pass through the
      adapted trial (face-gate hardened to survive-the-attack).
- [x] `DIGEST.md` reflects the shared harness in one place, not three.
- [x] Cost note: only judgment-call findings trigger the trial; mechanical rungs and facts don't
      (stated in the skill section and in cultivate's trial block).

## Open questions / honest caveats (resolve while executing)

1. **The craft verdict is fuzzier than the truth one.** "Survive the attack that it's ugly" is a
   discipline against over-confident "done," not a precision metric — don't dress it up as
   measurable the way winnow's true/false verdicts are.
2. **Cost.** Cultivate/flourish inherit the multi-call trial cost. It's bounded because judgment-call
   findings are rare per run, but confirm that on a real repo before declaring it cheap.
3. **Don't over-abstract into code.** The commands are prompt-driven; the shared thing is a skill
   section, not a shared function. `run_bench.py:judge` is the benchmark's reference implementation
   only — resist the urge to make the product commands import it.
4. **Blocking stays reversible-by-human.** Cultivate is the only command that blocks; a wrong block
   is expensive, so the trial reduces false blocks but the override is non-negotiable.
5. **No cross-run memory.** Same caveat as winnow: evergreen doesn't iterate, so there's no
   escalation ledger — each finding is tried on its own, once.

## Reference

Trial implementation (benchmark): `eval/bench/run_bench.py` — `judge()`, `snap_call`,
`challenge_call`, `run_prongs`, `blindspot_call`, `synthesis_call`.
Skill discipline: `skills/evergreen/SKILL.md` — "Put the verdict on trial" (the shared harness).
Commands: `commands/{winnow,cultivate,flourish}.md`.
