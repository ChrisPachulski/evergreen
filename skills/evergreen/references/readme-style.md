# readme-style — the gold standard `flourish` rewrites toward

Distilled from a survey of 28 READMEs in 50K★+ repos (React, Vue, Svelte, Next.js, Tailwind,
Vite, Bootstrap, Node, Deno, Rust, Go, VS Code, Electron, esbuild, TensorFlow, PyTorch,
Transformers, LangChain, Ollama, FastAPI, Django, freeCodeCamp, awesome, free-programming-books,
Oh My Zsh, Supabase, n8n, shadcn/ui). The convergence across all four batches was strong — this
is the shared anatomy, not one project's taste.

This is a *target*, not a checklist to bolt on. flourish rewrites an accurate-but-ugly doc
toward this shape, then the verify pass proves every claim against the code. Beauty here, truth
there.

## The spine (top-to-bottom section order)

1. **Visual hero** — centered logo or banner (dark/light `<picture>` variants = polish signal).
2. **Badges** — one row directly under the title.
3. **One-line value proposition** — the single most important line in the file.
4. *(optional)* collapsible Table of Contents — only for long docs.
5. **Key features** — bulleted, each with a **bolded lead descriptor**.
6. **Quick start** — install + one minimal, runnable first-use example.
7. *(if the product is visual)* one screenshot/diagram that *proves* a claim.
8. **Link to full docs** — the README is a gateway, not the manual.
9. **Community / support**, **Contributing**, **License** (license is universally last).

Reference-index repos (awesome lists) collapse 5–8 into one link directory. Big governed
projects (Node, VS Code) collapse the middle and link out, spending length on governance.

## Always present

- A **one-sentence value proposition near the very top** (near-unanimous).
- A **link out to full docs** — depth lives elsewhere; the README opens the door.
- **Contributing + License** pointers at the foot.
- A **badge row** under the title (when used — optional, never load-bearing).
- A **logo/hero** as the first element (most; text-only is the minimalist exception).

## Never present (unanimous — these are what make a README a "monstrosity")

Changelog / version history · roadmaps / planning notes · aspirational "coming soon" features ·
internal TODOs · walls of unbroken prose · full API reference inline (always linked out) ·
feature-comparison matrices · benchmark dumps (a single purposeful chart is the rare exception).

> These overlap exactly with what `cultivate` hunts at the repo level. If flourish meets a
> README stuffed with changelog/planning, it *cuts* — moving that content out, not prettying it
> up in place.

## The opening (the 1–3 lines that carry the file)

Formula: **`[Name] is a [category] that [core benefit].`** — present tense, ~10–15 words, names
the category in the first clause. Verbatim exemplars:

- "React is a JavaScript library for building user interfaces."
- "Go is an open source programming language that makes it easy to build simple, reliable, and efficient software."
- "Supabase is the Postgres development platform."

Sanctioned variations: **problem-first** (state the pain, then the goal — esbuild),
**social-proof** ("Used by some of the world's largest companies, Next.js…"), or **personality
right after** the definitional line (Supabase: "Sounds boring. Let's try again."). No
throat-clearing — line one says what it is and who it's for.

## Prose discipline

- Short declarative sentences (6–20 words; trend shortest in big-project READMEs).
- Present tense, second person ("you already created…"), imperative for steps ("Download," "Run").
- **Bullets for anything enumerable** (features, options, platforms) — with bolded lead
  descriptors. Paragraphs only for the value prop and section intros. Scannable, not narrative.
- Confident, plain, jargon-light; occasional play is fine. Zero filler.

## Visuals (earned, never decorative)

Hero (identity) + at most one screenshot (proof, placed right after the feature list, only if
the product has a visual surface) + optional one diagram (how it works). Never a gallery.
Dev-tool READMEs often ship **zero** images — their demo is a code block, not a screenshot.

## Badges

One cluster under the title: CI/build status, package version, license, downloads, community
(Discord), sometimes security/contributors. Hyperlinked. Optional — their absence is fine; they
are trust-signal garnish, never the substance.

## Code examples (the FastAPI / Ollama gold standard)

- **Install**: a single copy-pasteable shell block (or a short per-package-manager bullet list).
  One command to the minimal path; a fuller option (Docker) *after*.
- **First use**: ONE minimal, genuinely runnable snippet — real names, no error-handling padding.
- The gold pattern is a narrated **three beat — Create it → Run it → Check it**: each beat is one
  imperative sentence then a fenced block, and it **shows the terminal output / expected
  response**, not just the source.
- Collapsible `<details>` for variations (async, alt install) keeps the main path clean.

## Length

Bimodal — pick the archetype, don't split the difference:

- **Gateway / landing (~80–450 words)** — value prop + features + quick start + links; depth
  offloaded to docs. The modern default; reach for this unless the project genuinely needs more.
- **Onboarding / governed (~1,200–2,500 words)** — adds inline quick-start, governance, team.
  Justified only when the project demands it; still tight, never sprawling.

## Skeleton (a flourish target, fill from the code)

```md
<p align="center"><img src="…logo…" alt="Name"></p>
<p align="center"><em>One-line value proposition.</em></p>
<p align="center">[badges]</p>

Name is a [category] that [core benefit].

## Features
- **Lead descriptor** — what it does.
- **Lead descriptor** — what it does.

## Quick start
\`\`\`sh
[one install command]
\`\`\`
\`\`\`[lang]
[one minimal runnable example]
\`\`\`

## Documentation
Full docs at [link].

## Contributing
See [CONTRIBUTING]. ## License [MIT](LICENSE).
```
