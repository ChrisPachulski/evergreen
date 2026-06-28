# Moved path drift (rung 1 — vanished path)

The quickest mechanical check: a doc points at a file that isn't there anymore.

## The drift

```
docs/cli.md:8        "See config/legacy.json for options."
config/legacy.json   deleted; moved to config/templates/legacy-preset.json
```

## What evergreen says

```
[high] in_docs_not_code  docs/cli.md:8 — cites config/legacy.json (no longer on disk) → fix path
```

## Proposed fix (derivable → diff)

```diff
- See config/legacy.json for options.
+ See config/templates/legacy-preset.json for options.
```

## Why it matters

Rung 1 is the cheapest rung: grep the docs for in-repo paths, confirm each exists. A doc naming a
file that's gone is drift — evergreen points at the line and shows the path it can find; you confirm
it's the right replacement.
