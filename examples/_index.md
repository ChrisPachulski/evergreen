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

## Beyond drift — the craft & hygiene axes

The reflex above proves *truth*. The two on-demand commands prove it too, in their own axis —
`flourish` (craft) and `cultivate` (hygiene).

| Example | Axis | What it shows |
|---|---|---|
| [flourish-craft](flourish-craft.md) | craft | An accurate-but-ugly README restructured to the gold standard — every claim still code-backed |
| [cultivate-orphan](cultivate-orphan.md) | hygiene | An orphaned 6.4 MB asset a filename grep can't see — caught by the reference graph, not rubber-stamped "clean" |
