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

Rules:
- **Prove it or drop it.** Cite the code that makes the doc wrong, or it isn't a finding. A rung-3/4
  flag goes on trial before it ships — a snap call, a challenge it must survive (in either
  direction), then three independent blind reads (defend / prove-wrong / hardest-broken) weighed
  into the verdict, never a veto. Prove by test is the default where the code runs: a failing test
  the doc backs is drift; a test that won't run is inconclusive.
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
