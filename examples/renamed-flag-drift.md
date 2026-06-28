# Renamed flag drift (rung 2 — dead contract)

The cheapest, most painful drift: a flag you renamed that the docs still cite.

## The drift

```
README.md:42   "Run with `--workers 8` to set concurrency."
cli.py:156     parser.add_argument("--concurrency", ...)   # was --workers
```

`--workers` no longer exists in the code.

## What evergreen says

```
[high] in_docs_not_code  README.md:42 — documents `--workers`, gone from cli.py:156 → fix
```

## Proposed fix (derivable → diff)

```diff
- Run with `--workers 8` to set concurrency.
+ Run with `--concurrency 8` to set concurrency.
```

## Why it matters

You renamed a flag and moved on. Three files still document the old name. Nobody notices until a
user copies a broken command. Rung 2 is a grep: for every flag the doc names, is it still in the code?
