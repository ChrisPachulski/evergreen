# logloom

logloom reads structured log files — JSON Lines or logfmt — filters records by level, and
prints a one-line `kept=N malformed=M` summary. It is a single-purpose tool: it does not tail,
ship, or store anything, and it runs single-threaded on purpose.

## How the pieces fit together

There are three modules and no other moving parts. `logloom/parser.py` owns the wire formats:
a `PARSERS` dict maps each format name to a callable that turns one line into a flat dict, so
adding a format is one function and one dict entry. `logloom/config.py` owns layered
configuration and exposes a single `load_config()` that returns a plain merged dict.
`logloom/cli.py` wires the two together: argparse for flags, one pass over each input file,
and the summary line at the end. The CLI is also installed as a `logloom` console script via
`pyproject.toml`.

## Parsing internals

`parse_json_line` calls `json.loads` and rejects any line whose top-level value is not an
object. `parse_logfmt_line` splits on whitespace and partitions each token on `=`; a bare
token with no `=` raises `ValueError`, and surrounding double quotes are stripped from values.
Both parsers signal failure the same way — `ValueError` — which the CLI catches: a malformed
line increments a counter and is skipped, never fatal.

## Configuration model

Three layers, later wins: the `DEFAULTS` dict in `config.py`, then the `[logloom]` table of a
`loom.toml` in the working directory, then the `LOGLOOM_FORMAT` / `LOGLOOM_LEVEL` environment
variables. The merge only ever overrides keys that already exist in `DEFAULTS`.

### Config gotchas

| Gotcha | Detail |
| --- | --- |
| Env always wins | A stray `LOGLOOM_FORMAT` in a crontab silently overrides `loom.toml`; nothing warns. |
| Unknown env vars are ignored | Only keys in `DEFAULTS` are read from the environment — `LOGLOOM_FMT` or `LOGLOOM_OUTPUT` do nothing, silently. |
| Empty level means everything | `level = ""` keeps every record; there is no `all` keyword. |
| The table name is load-bearing | Keys at the top level of `loom.toml` are ignored — they must sit under `[logloom]`. |

## Running it

Install with `pip install .` (Python 3.11+), which puts `logloom` on PATH, or run the module
directly with `python -m logloom.cli`. Point it at one or more files and optionally pin the
format and level:

    logloom app.log worker.log --format logfmt --level error

### Exit codes

`0` — every line parsed. `1` — at least one malformed line was counted and skipped. `2` — the
`--parallel` flag was passed: it is still parsed so old wrapper scripts don't crash, but
logloom refuses it and stays single-threaded.
