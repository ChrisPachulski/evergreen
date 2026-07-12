# Evergreen — session digest

Keep docs true to the code *right now*. When a response changes code that has docs, or writes/
reviews docs, surface a one-line verdict — the finding(s), or `evergreen: docs still match`.
Silent only when nothing documented was touched. Off with "stop evergreen" or `/evergreen off`.

Candidate set = what changed: grep the docs for the touched file paths and edited symbol names,
not the whole tree. Walk the rungs in order per claim; cheap mechanical checks before semantic:

1. **Vanished path** — a doc names an in-repo path that no longer exists on disk. Grep, confirm.
2. **Dead contract** — a documented public surface (CLI flag, env key, export, route, type,
   field) gone from the code. Check the diff for a rename first — a rename is a reconcile.
3. **Drifted snippet/signature** — a fenced block, signature, or schema no longer matching source.
4. **Semantic drift** (strict only) — does the prose still describe current behavior? A claim the
   code can't settle → `behavior-asserted — verify manually`; never pass or guess.

Release identity is a living claim. On release/version/TestFlight/App Store work:
- Find the checked-in source of truth; for Xcode/XcodeGen, distinguish `MARKETING_VERSION`
  (`CFBundleShortVersionString`) from monotonically increasing `CURRENT_PROJECT_VERSION`
  (`CFBundleVersion`). Never patch a generated project when a manifest owns it.
- Ordinary commits/rebuilds change neither counter. Same-version candidate → build only
  (`0.9.0 (72)` → `0.9.0 (73)`); declared patch-only release → patch + build
  (`0.9.0 (72)` → `0.9.1 (73)`); feature milestone → minor + build.
- Audit product milestones since the marketing version last changed. Do not decide from commit
  count alone; put the milestone judgment on trial. Proven staleness is `release_identity_drift`.
- Next build = max(local, every related platform's published build) + 1. Without portal evidence,
  use local + 1, say `external build state unverified`, and never call it upload-safe.
- Keep related Universal Purchase app/extension targets aligned, regenerate, inspect every target's
  resolved settings, and run release preflight. Never upload, push, or mutate a portal without
  explicit authority.
- Worked behavior: eight evidenced pre-1.0 milestone waves can justify `0.1.0 (71)` →
  `0.9.0 (72)`; this is evidence-guided, not a fixed arithmetic formula.

Rules:
- **Prove it or drop it.** Cite the code that makes the doc wrong, or it isn't a finding. Every
  judgment-call verdict in the family — a rung-3/4 drift flag, cultivate's delete/block, flourish's
  "done" — goes on the same trial before it ships: a snap call, a challenge it must survive (in
  either direction), three independent blind reads (winnow's: defend / prove-wrong /
  hardest-broken), a blind-spot pass, weighed into the verdict, never a veto. Mechanical facts
  (a grep, a reference count) skip the trial. Prove by test is the default where the code runs: a
  failing test the doc backs is drift; a test that won't run is inconclusive.
- Read the changed file at HEAD, not just the diff's `+` lines — rot lives in old comments.
- Code is truth; the doc is the claim. Documented-but-missing = finding; undocumented = informational.
- Exempt what leads or freezes: specs/ADRs/RFCs/roadmaps/plans, CHANGELOG history, dated
  snapshots. Age is not drift.
- Not your contracts: third-party flags, cross-repo paths, URLs, generic symbols, CSS custom
  properties. Honor `.evergreen-ignore`; never re-raise a flag rejected this session — and offer
  the one-line `.evergreen-ignore` entry that keeps a rejected flag dropped across sessions.
- Propose a diff only for the 1:1 code-derivable (dead path/flag/key, endpoint table, schema,
  mirrored snippet); flag-never-rewrite everything else. Never block a commit.

Output, per finding: `[high|med|low] category  file:line — what's wrong (cite the code) → fix | flag`
Exempt docs go on a trailing `left alone:` line, never as a finding.
Surface still matches → one line: `evergreen: docs still match`.

Full ruleset (taxonomy, flag-vs-winnow depths, the flourish/cultivate family): load the
`evergreen` skill on demand.
