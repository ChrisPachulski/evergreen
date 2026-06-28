---
description: Flourish a doc — aggressively restructure an accurate-but-ugly README up to gold-standard, then prove every claim against the code. Craft pass + truth pass. Emits a diff; never silently overwrites.
---

Run **flourish** using the **evergreen skill** — the craft axis. The ride-along reflex flags
prose and *never rewrites* it. `flourish` is the **sanctioned exception**: you were invoked to
**rewrite**, so rewrite. Do not let evergreen's "don't touch accurate prose" instinct talk you
out of the job — that instinct governs the reflex, not this command.

> **Accurate is not the bar.** An accurate README can still be a monstrosity: architecture-first
> ordering, no hero, no value proposition, no feature list, walls of dense prose, and — for a
> visual product — not one screenshot. A doc that is *true and unreadable* is exactly what
> flourish exists to fix. "It's already accurate" is **not** "it's already good." If you finish
> and the structure barely moved, you under-did the job — run it again, harder.

Argument given: `{{args}}`.

**Scope.** Default: the one file path given. With `--all`: every documentation file in the repo,
dropping the exempt set (specs/ADRs/roadmaps/CHANGELOG history/dated snapshots). Never flourish a
frozen record.

**The "why" — auto by default.** A well-written codebase makes its rationale legible, so deriving
it is the default, not a punt. With `--manual`, never invent rationale — marker it instead.

## Procedure (per target doc)

1. **Read the project, not just the doc.** Before rewriting, gather the raw material the gold
   standard needs: the package manifest (name, scripts, entry points), the real feature set,
   and — critically — **search the repo for visual assets** (`public/`, `assets/`, `.github/`,
   `docs/`, `static/` for `*.svg`/`*.png`/logo/screenshot). You cannot write a great hero or
   feature list from the old README alone.

2. **Craft pass — impose the gold-standard skeleton** (`skills/evergreen/references/readme-style.md`).
   This is restructuring, not light editing. The top of the result MUST be, in order:
   - **Hero** — a centered logo/mark if the repo has one; otherwise a clean centered title block.
   - **One-line value proposition** as the first prose — a single sentence (`[Name] is a
     [category] that [benefit]`), *not* a paragraph. Pull it from the product/docs if buried.
   - **Badges** — one row (stack + license at minimum; CI/version where they exist).
   - **Features** — scannable bullets, each with a **bold lead descriptor**. Never a wall of prose.
   - **Quick start** — install + one minimal runnable example, **near the top**, before deep
     architecture. Push long/optional setup into collapsible `<details>` to keep the main path clean.
   - **Visual proof** — if the product has a UI and **no screenshot exists**, insert a clearly
     marked placeholder with a `<!-- TODO(screenshot): ... -->` and tell the user it's the
     highest-impact addition. Never silently skip it — a screenshotless visual product is the
     single most common README failure.
   Then: **demote** the deep technical detail (full architecture, exhaustive dev setup, API
   surface) *below* the visitor-facing top, and **cut** what great READMEs never carry — changelog
   dumps, roadmaps, planning notes, aspirational features, inline API reference. Move it out or
   into linked docs; don't prettify it in place. Fill *derivable* gaps from the code (install from
   the manifest, usage from the CLI/exported API, a runnable example). Write the "why" from code
   evidence (auto) or `<!-- evergreen: fill in -->` marker it (`--manual`).

   Restructuring and reordering are **not** the fabrication the verify pass guards against — you
   are rearranging and re-voicing claims the code already backs, not inventing new ones.

3. **Verify pass** — run the **freshness ladder** (winnow depth) on the *rewritten* doc:
   - Every factual claim and every auto-written "why" must cite the code (rungs 1–4). New badges
     and feature bullets are claims too — each must be true (don't badge a framework the repo
     doesn't use).
   - A claim the code can't back is **cut** — a fabricated fact dropped, an ungrounded rationale
     reduced to a marker. The honesty guardrail holds even in auto: a "why" with **no trace in
     code** (pure business/regulatory/external intent) is markered, never invented.

4. **Emit the rewrite for approval.** Show it; never write silently committed. With `--all`, one
   per doc.

## The monstrosity test (run before you call it done)

Hold the result against `readme-style.md`. If **any** of these is false, you haven't finished:
the first screenful is hero → value prop → features → quick start (not architecture); a visual
product shows a screenshot or a marked placeholder; features are bullets, not paragraphs; the
deep internals sit *below* the visitor top. A doc only "needs no change" if it already passes
this test — almost none do. Open with a one-line read of what you restructured, the verify verdict
(certified / cut / markered), then the diff.
