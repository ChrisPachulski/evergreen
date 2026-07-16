<h1 align="center">logloom</h1>

<p align="center"><em>Reads your logs, counts the damage, says nothing else.</em></p>

<p align="center">
  <img alt="python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-blue">
  <img alt="single-threaded on purpose" src="https://img.shields.io/badge/threads-one%2C%20on%20purpose-success">
</p>

logloom reads structured log files — JSON Lines or logfmt — filters records by level, and
prints a one-line `kept=N malformed=M` summary. Point it at one or more files and read the
last line; that is the whole interface.

## Highlights

- A malformed line increments a counter and is skipped, never fatal.
- It does not tail, ship, or store anything, and it runs single-threaded on purpose.
- Adding a format is one function and one dict entry.
- Three layers, later wins: the `DEFAULTS` dict in `config.py`, then the `[logloom]` table of a `loom.toml` in the working directory, then the `LOGLOOM_FORMAT` / `LOGLOOM_LEVEL` environment variables.

## Running it

Install with `pip install .` (Python 3.11+), which puts `logloom` on PATH, or run the module
directly with `python -m logloom.cli`. Point it at one or more files and optionally pin the
format and level:

```
logloom app.log worker.log --format logfmt --level error
```

```
+-----------+     +---------+     +--------------------+
| app.log   | --> | logloom | --> | kept=N malformed=M |
+-----------+     +---------+     +--------------------+
```

### Exit codes

`0` — every line parsed. `1` — at least one malformed line was counted and skipped. `2` — the
`--parallel` flag was passed: it is still parsed so old wrapper scripts don't crash, but
logloom refuses it and stays single-threaded.

## Configuration model

Three layers, later wins: the `DEFAULTS` dict in `config.py`, then the `[logloom]` table of a
`loom.toml` in the working directory, then the `LOGLOOM_FORMAT` / `LOGLOOM_LEVEL` environment
variables. The merge only ever overrides keys that already exist in `DEFAULTS`.

## Under the hood

Deep dives, demoted not deleted — expand what you need.

<details>
<summary>How the pieces fit together</summary>

### How the pieces fit together

There are three modules and no other moving parts. `logloom/parser.py` owns the wire formats:
a `PARSERS` dict maps each format name to a callable that turns one line into a flat dict, so
adding a format is one function and one dict entry. `logloom/config.py` owns layered
configuration and exposes a single `load_config()` that returns a plain merged dict.
`logloom/cli.py` wires the two together: argparse for flags, one pass over each input file,
and the summary line at the end. The CLI is also installed as a `logloom` console script via
`pyproject.toml`.

</details>

<details>
<summary>Parsing internals</summary>

### Parsing internals

`parse_json_line` calls `json.loads` and rejects any line whose top-level value is not an
object. `parse_logfmt_line` splits on whitespace and partitions each token on `=`; a bare
token with no `=` raises `ValueError`, and surrounding double quotes are stripped from values.
Both parsers signal failure the same way — `ValueError` — which the CLI catches: a malformed
line increments a counter and is skipped, never fatal.

</details>
