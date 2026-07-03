---
description: Flourish a doc — restructure an accurate-but-ugly README up to gold-standard, then prove every claim against the code. Craft pass + truth pass. Emits a diff; never silently overwrites.
---

Run **flourish** using the **evergreen skill** — the craft axis. The reflex flags prose and never
rewrites it; flourish is the sanctioned exception — you were invoked to **rewrite**, so rewrite. The
"don't touch accurate prose" instinct governs the reflex, not this command.

Accurate is not the bar, and neither is *structured*. A true-but-unreadable doc (architecture-first,
no hero, no value prop, walls of prose) is what flourish fixes — but a doc that's structurally correct
and **voiceless** is only half-fixed. The bar is the ilk of this repo's own `README.md`: a hook that
makes you feel the problem, a tagline with a point of view, concrete stakes, personality in the seams
— all still code-backed. If you finish and the structure moved but the result reads neutral, you
stopped at the skeleton. Run the voice pass.

Argument: `{{args}}`. Default scope: the one file path given. With `--all`: every documentation file,
dropping the exempt set (specs/ADRs/roadmaps/CHANGELOG history/dated snapshots). Never flourish a
frozen record.

The "why" is auto-derived from code by default. With `--manual`, never invent rationale — marker it.

## Procedure (per target doc)

1. **Read the project, not just the doc.** Gather raw material: the package manifest (name, scripts,
   entry points), the real feature set, and visual assets (search `public/`, `assets/`, `.github/`,
   `docs/`, `static/` for `*.svg`/`*.png`/logo/screenshot).

2. **Craft pass — impose the gold-standard skeleton** (`skills/evergreen/references/readme-style.md`).
   Restructuring, not light editing. Top of the result, in order:
   - **Hero** — centered logo/mark if the repo has one, else a clean centered title block.
   - **One-line value proposition** as the first prose (`[Name] is a [category] that [benefit]`), not
     a paragraph. Pull it from the product/docs if buried.
   - **Badges** — one row (stack + license minimum; CI/version where they exist).
   - **Features** — scannable bullets, each with a **bold lead descriptor**. Never a wall of prose.
   - **Quick start** — install + one minimal runnable example, near the top, before deep architecture.
     Push long/optional setup into collapsible `<details>`.
   - **Visual proof** — a screenshot is highest-impact for a UI product. If you can run the app,
     capture and embed one. Otherwise mark the spot with an **invisible HTML comment only**
     (`<!-- screenshot: ... -->`) pointing at a **tracked** path — run `git check-ignore` first
     (`docs/` is often gitignored; prefer `public/`/`assets/`/`.github/`). Never a visible
     placeholder box or "screenshot goes here" text. Raise a missing screenshot in your summary, not
     the doc.
   Then **demote** deep technical detail (full architecture, exhaustive dev setup, API surface) below
   the visitor-facing top, and **cut** what great READMEs never carry (changelog dumps, roadmaps,
   planning notes, aspirational features, inline API reference) — move it out or link it. Fill
   *derivable* gaps from the code (install from the manifest, usage from the CLI/exported API, a
   runnable example). Write the "why" from code evidence (auto) or `<!-- evergreen: fill in -->`
   marker it (`--manual`). Restructuring is not fabrication — you rearrange claims the code already
   backs, not invent new ones.
   - **Score the face, not just the spine.** Before any craft verdict, enumerate these
     gold-standard face elements as present/absent: (a) centered visual hero (`<h1 align="center">`
     or logo), (b) a one-line epigraph with voice under the title, (c) a badge row, (d) a
     vivid/problem-first opener before the definitional line, (e) at least one earned visual
     (ASCII "how it works", diagram, or screenshot). A doc with a correct spine but a dead face is
     not at the gold standard — it is "accurate but ugly," which is exactly flourish's job.
   - **Diff against a shipped exemplar.** Open one concrete gold-standard README (this plugin's
     own, or one from the 28-repo survey) and compare the target's shape and voice to it — not
     only the abstract style doc. The exemplar catches what the checklist misses.

3. **Voice pass — make it sing** (the step that produces evergreen-ilk output, not just tidy output).
   The skeleton is now right; give it a voice against `readme-style.md`'s Voice section:
   - **Write a tagline with a point of view** — a dare, a stakes line, or a metaphor the product
     earns, distinct from the definitional value prop. Not "X keeps docs fresh" but evergreen's "The
     docs said yes. The code said no. Only one of them gets to be true."
   - **Lead with a hook when the product has a point of view** — dramatize the pain in concrete,
     lived terms before naming the tool (readme-style's hook-first register). Prefer this to the bare
     `[Name] is a [category]` formula for any tool with an opinion.
   - **Put personality in the seams** — section intros and FAQ answers, not just the hero.
   - **Show it working** — for a dev tool, a before/after or a real slice of the tool's own output
     beats a prose claim.
   - **Don't-flatten guard:** if the source doc *already* has a strong hook or voice, your job is to
     sharpen and keep it, **never** to replace an evocative opening with the definitional formula.
     Flattening a doc that had voice is a failed flourish, even if the structure is now textbook.
   Voice is not fabrication: every line it adds still faces step 4. This is prose-shaping of claims the
   code backs, plus a marker for any rationale it can't.

4. **Verify pass — the truth trial** — run the freshness ladder (winnow depth) on the *rewritten*
   doc. Every factual claim and auto-written "why" must cite the code (rungs 1–4); new badges and
   feature bullets are claims too. Judgment-call verdicts here go through the winnow trial
   **verbatim** — same claim, verdict space, and prongs as `/evergreen:winnow`, per the skill's
   "Put the verdict on trial". A claim the code can't back is **cut**; an ungrounded rationale is
   reduced to a marker. A "why" with no trace in code (pure business/regulatory/external intent)
   is markered, never invented.

5. **Emit the rewrite for approval.** Show it; never write silently. With `--all`, one per doc.

## The monstrosity test (before you call it done)

Hold the result against `readme-style.md`. Two floors — **structure and voice** — and failing either
means you're not done.

Only conclude "already at gold standard, no changes" after showing the face scorecard (the five
elements from the craft pass, each present/absent) **and** the spine check — both must pass. "Spine
present" alone never justifies changing nothing; a voiceless doc with correct sections still gets
the craft pass.

**Structure floor.** First screenful is hero → value prop → features → quick start (not
architecture); a visual product shows a screenshot or an invisible comment marker (never a visible
"screenshot goes here" box); features are bullets, not paragraphs; deep internals sit below the
visitor top; the never-present set (changelog/roadmap/aspirational/inline API) is cut, not prettied.

**Voice floor** (the one flourish keeps skipping). Read the first three lines aloud: do they make you
*feel the problem*, or do they only define the tool? Is there a tagline with a point of view, distinct
from the value prop? Do section intros / FAQ carry personality, or are they generic? If the source had
a voice, is the rewrite at least as evocative — or did you flatten it to the template? A README that
passes structure but reads neutral **fails this test**. The bar is this repo's own README; if yours is
plainer than that, run the voice pass again.

**Put "done" on trial — the craft trial.** The two floors are the evidence; the verdict that you've
cleared them is a judgment, and it runs the skill's shared harness, "Put the verdict on trial",
with flourish's craft parameters. A scorecard read once is a checklist; a scorecard that survives
an attack is a verdict — this is what stops the structurally-correct-but-voiceless doc that has
sailed through before.

- **claim / snap:** "this rewrite is at the gold standard — ship it."
- **challenge (must survive):** "no — it's still flat: here's the definitional opener with no hook,
  the missing centered hero, the toneless section intro." A "looks done" that can't beat "it's
  still ugly" does not ship.
- **three blind reads:** *defend* it's done / *attack the prose* (voice, hook, tagline,
  personality in the seams) / *attack the structure* (hero, spine, badges, earned visual).
- **blind-spot:** "did everyone miss that a badge or feature bullet claims something the code
  can't back?" — a hit here routes back to the truth trial (step 4), because a beautiful lie is
  still a lie.

The craft verdict is fuzzier than the truth one — "survives the attack that it's ugly" is a
discipline against an over-confident "done", not a precision metric. Don't dress it up as one.

Open with a one-line read of what you restructured *and how you sharpened the voice*, the verify
verdict (certified / cut / markered), then the diff.
