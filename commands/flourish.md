---
description: Flourish a doc — craft an accurate-but-ugly README up to gold-standard, then prove every claim against the code. Beauty pass + truth pass. Emits a diff; never silently overwrites.
---

Run **flourish** using the **evergreen skill** — the craft axis. The ride-along reflex flags
prose and *never rewrites* it; `flourish` is the sanctioned exception, opened only by your
explicit request. It takes a doc that is *accurate but ugly* and crafts it toward the gold
standard, then runs the freshness ladder on its own rewrite so it can't ship a single claim the
code won't back. **Beauty is the craft pass; truth is the verify pass.**

Argument given: `{{args}}`.

**Scope.** Default: the one file path given. With `--all`: every documentation file in the repo,
dropping the exempt set (specs/ADRs/roadmaps/CHANGELOG history/dated snapshots — the same
exemptions the reflex honors). Never flourish a frozen record.

**The "why" — auto by default.** A well-written codebase makes its rationale legible, so deriving
it is the default, not a punt. With `--manual`, never invent rationale — marker it instead.

## Procedure (per target doc)

1. **Craft pass** — rewrite toward `skills/evergreen/references/readme-style.md`:
   - Restructure to the spine (hero → value prop → features → quick start → docs link → license);
     kill walls-of-text; use bullets with bolded lead descriptors; tighten prose (ponytail voice).
   - **Cut** what great READMEs never carry: changelog dumps, roadmaps, planning notes,
     aspirational features, inline API reference. Move it out, don't prettify it in place.
   - Fill *derivable* gaps from the code: install from the package manifest, usage from the CLI
     surface / exported API, a minimal example that actually runs.
   - Write the "why" from code evidence (auto), or `<!-- evergreen: fill in -->` marker it
     (`--manual`).

2. **Verify pass** — run the **freshness ladder** (the winnow depth) on the *rewritten* doc:
   - Every factual claim and every auto-written "why" must cite the code (rungs 1–4).
   - A claim the code can't back is **cut** — a fabricated fact dropped, an ungrounded rationale
     reduced to a `<!-- evergreen: fill in -->` marker. The honesty guardrail holds even in auto:
     a "why" with **no trace in code** (pure business/regulatory/external intent) is markered,
     never invented.
   - With prove-true active this is certify-or-surface, not just lie-hunting.

3. **Emit a diff** of the full proposed rewrite and **await approval**. Never write silently.
   With `--all`, present one diff per doc.

Open with a one-line read of what the craft pass changed, then the verify-pass verdict
(certified / cut / markered), then the diff. If a doc is already at the gold standard, say so and
change nothing.
