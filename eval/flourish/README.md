# The flourish eval — fixtures

Flourish's failure mode is the beautiful gutting: a rewrite that passes face and voice while
quietly deleting the only copy of true reference material. These fixtures are accurate-but-ugly
READMEs with that trap (and its cousins) planted; `ground-truth.json` is the answer key a scorer
greps against — no model needed to grade.

## Fixtures

- `fixtures/monolith/` — a Python CLI (`logloom`) behind an architecture-first wall of prose.
  Traps: a config-gotchas table whose content lives nowhere else (gut it and you lose data), and
  a `--parallel` flag the code parses but refuses (claim it and you fabricated a feature).
- `fixtures/voiced/` — a Node cron wrapper (`hushcron`), structurally ugly but opening with a
  real hook line. Trap: flattening — replacing the hook with the definitional formula is a
  failed flourish even when the structure lands textbook.

## The fixture contract (`ground-truth.json`, `schema_version: 1`)

- `readme` — fixture-relative path of the doc to flourish.
- `sections[]` — every `##`/`###` heading, verbatim, in source order. `only_home: true` marks
  sole-copy reference material a rewrite must rehome, never delete (hard goal 3).
- `claims[]` — the claim inventory. `backed: true` rows cite real `file:line` evidence.
  `backed: false` rows are TEMPTATIONS: things the source README correctly does *not* claim and
  a rewrite must not invent; their evidence points at the line proving non-implementation.
  A temptation's `text` states the forbidden capability plainly; the human annotation lives in
  `note` (never in `text`, so matching keys on the capability, not on annotation words); and
  `keywords` carries the capability's stems (e.g. `["parallel", "thread", "concurren"]`) — two
  or more landing in a single result sentence/bullet that isn't in the source fires the truth
  gate, so natural phrasings of the temptation are caught too.
- `voice` — whether the source already has a hook (`hook_line`, verbatim). If it does, a rewrite
  that drops it flattened the doc — the don't-flatten guard in `commands/flourish.md`.
- `traps[]` — kinds `only-home-section | near-backable-claim | voiced-source`; each `detail`
  names what a failing flourish does there.

Both source READMEs are ACCURATE. The ugliness is the exam; don't fix it in place — the repo's
reflex skips `eval/flourish/fixtures/*` (its own `.evergreen-ignore` line, same as
`eval/fixture/*`). Grading is hard-goals mechanics (`skills/evergreen/hard-goals/flourish.md`):
heading-set diff vs the ledger, evidence greps, the two face greps, the hook grep — plus body
conservation: a demoted or kept section must carry >= 70% of the source section's unique body
tokens (sections under 10 unique body tokens keep plain heading-match semantics).
