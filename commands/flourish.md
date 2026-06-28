---
description: Flourish a doc — restructure an accurate-but-ugly README up to gold-standard, then prove every claim against the code. Craft pass + truth pass. Emits a diff; never silently overwrites.
---

Run **flourish** using the **evergreen skill** — the craft axis. The reflex flags prose and never
rewrites it; flourish is the sanctioned exception — you were invoked to **rewrite**, so rewrite. The
"don't touch accurate prose" instinct governs the reflex, not this command.

Accurate is not the bar. A true-but-unreadable doc (architecture-first ordering, no hero, no value
prop, walls of prose, no screenshot for a visual product) is exactly what flourish fixes. If you
finish and the structure barely moved, run it again, harder.

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

3. **Verify pass** — run the freshness ladder (winnow depth) on the *rewritten* doc. Every factual
   claim and auto-written "why" must cite the code (rungs 1–4); new badges and feature bullets are
   claims too. A claim the code can't back is **cut**; an ungrounded rationale is reduced to a marker.
   A "why" with no trace in code (pure business/regulatory/external intent) is markered, never invented.

4. **Emit the rewrite for approval.** Show it; never write silently. With `--all`, one per doc.

## The monstrosity test (before you call it done)

Hold the result against `readme-style.md`. If any is false, you haven't finished: first screenful is
hero → value prop → features → quick start (not architecture); a visual product shows a screenshot or
an invisible comment marker (never a visible "screenshot goes here" box); features are bullets, not
paragraphs; deep internals sit below the visitor top. Open with a one-line read of what you
restructured, the verify verdict (certified / cut / markered), then the diff.
