# readme-style — the gold standard `flourish` rewrites toward

The shared anatomy of 28 READMEs from 50K★+ repos. A *target*, not a checklist: flourish rewrites an
accurate-but-ugly doc toward this shape **and voice**, then the verify pass proves every claim against
the code. Structure gets a visitor oriented; **voice makes them care** — the two pillars, not one.

**The living exemplar is this repo's own `README.md`.** When you flourish, the bar is "of that ilk":
a hook that makes you feel the problem, a tagline with a point of view, concrete stakes, personality
in the seams — all still code-backed. If your output reads more neutral than evergreen's README, you
imposed the skeleton and stopped short of the voice pass. Go back.

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
- A **logo/hero** as the first element. Text-only is a narrow exception — a bare CLI/dev-tool
  whose demo is a code block. A config, product, library, or anything a human lands on gets a
  centered hero + a voiced epigraph; do not invoke "minimalist" to skip the face.

## Never present (unanimous — these are what make a README a "monstrosity")

Changelog / version history · roadmaps / planning notes · aspirational "coming soon" features ·
internal TODOs · walls of unbroken prose · full API reference inline (always linked out) ·
feature-comparison matrices · benchmark dumps (a single purposeful chart is the rare exception).

> These overlap exactly with what `cultivate` hunts at the repo level. If flourish meets a
> README stuffed with changelog/planning, it *cuts* — moving that content out, not prettying it
> up in place.

## The opening (the 1–3 lines that carry the file)

Two registers, both gold — pick by product, and know which you're reaching for.

**Definitional — the floor.** `[Name] is a [category] that [core benefit].` Present tense, ~10–15
words, category in the first clause. Safe, clear, never wrong:

- "React is a JavaScript library for building user interfaces."
- "Go is an open source programming language that makes it easy to build simple, reliable, and efficient software."
- "Supabase is the Postgres development platform."

**Hook-first — the ceiling.** Make the reader *feel the problem* before you name the tool — a one-line
sting, a metaphor, a stakes-raising scenario — then land the value prop. This is what separates a
README people remember from one they skim, and it's what this repo's own README does: a tagline
that's a dare ("The docs said yes. The code said no. Only one of them gets to be true."), then a
paragraph that dramatizes the pain ("Your README was true the day you wrote it. Then a flag got
renamed, a file moved… the docs stayed exactly where they were") *before* it ever says what evergreen
is. A tool with a sharp point of view should reach for this, not the bare formula.

The formula is the floor you must clear; the hook is the ceiling you should aim for when the product
has a point of view. Never open with install steps, a TOC, or "Welcome to" — line one either says
what it is or makes you feel why it matters.

## Voice (substance, not garnish)

Structure orients; voice makes them care and remember. A structurally-perfect, voiceless README is
the *second* monstrosity — right after the architecture-first wall — and the one flourish is most
likely to ship if it stops at the skeleton. Earn a voice:

- **A tagline with a point of view** — not a restatement of the value prop. evergreen: "The docs said
  yes. The code said no." A dare, a stakes line, or a crisp metaphor the product actually earns.
- **Concrete over abstract** — "someone pastes a command that no longer exists" beats "documentation
  can drift." Name the failure the reader has lived; specifics are what make prose land.
- **Personality in the seams** — section intros and FAQ answers are where voice breathes. evergreen's
  FAQ: "Why 'evergreen'? A doc that stays true as the code grows is evergreen. Yours aren't. Yet."
- **A `show it working` beat** — for a dev tool with no screenshot, the demo *is* the proof: a
  before/after or a real slice of the tool's own output (evergreen's rendered `evergreen:` finding
  block) carries more than any prose claim. Show it doing the thing.
- **Match and amplify the project's register** — read the existing voice; a playful project stays
  playful, a serious one stays crisp. **Never sand an evocative doc down to neutral** to fit the
  template (see flourish's don't-flatten guard).

Voice is never a license to fabricate: every claim the voice makes still faces the verify pass.

## Prose discipline

- Short declarative sentences (6–20 words; trend shortest in big-project READMEs).
- Present tense, second person ("you already created…"), imperative for steps ("Download," "Run").
- **Bullets for anything enumerable** (features, options, platforms) — with bolded lead
  descriptors. Paragraphs only for the value prop and section intros. Scannable, not narrative.
- Confident, plain, jargon-light; occasional play is fine. Zero filler.
- **The opener should carry voice** — an evocative epigraph or a problem-first hook, not just the
  definitional sentence. "Accurate but toneless" is the ugliness flourish exists to remove; a
  bland-but-correct opener is a finding, not a pass.

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
