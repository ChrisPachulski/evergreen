# Examples — drift evergreen catches

One per rung of the freshness ladder, plus the case it correctly leaves alone. Each shows the
drift, evergreen's verdict, and whether it proposes a diff or just flags.

| Example | Rung | What it shows |
|---|---|---|
| [renamed-flag-drift](renamed-flag-drift.md) | 2 · dead contract | A CLI flag the docs still cite, gone from the code |
| [moved-path-drift](moved-path-drift.md) | 1 · vanished path | A doc naming a file that was moved/deleted |
| [drifted-code-snippet](drifted-code-snippet.md) | 3 · drifted snippet | A code example that no longer matches the source |
| [stale-prose-claim](stale-prose-claim.md) | 4 · semantic drift | Prose that was true, until the code changed under it |
| [adr-exempt-case](adr-exempt-case.md) | — · exempt | A doc that *looks* stale but is correctly left alone |

Rule above all: **prove it or drop it.** If evergreen can't cite the code that makes the doc wrong,
it isn't a finding.

## Beyond drift — the craft axis

The reflex above proves *truth*. `flourish` proves truth too, but its job is *craft* — taking a
doc that is accurate yet unreadable and restructuring it to the gold standard.

| Example | Axis | What it shows |
|---|---|---|
| [flourish-craft](flourish-craft.md) | craft | An accurate-but-ugly README restructured to the gold standard — every claim still code-backed |
