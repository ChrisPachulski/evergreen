# Stale prose claim (rung 4 — semantic drift)

A sentence that was true when written, and the code changed under it.

## The drift

```
README.md:15   "All CLI flags are required."
cli.py:42-67   --concurrency (required), --output (required),
               --log-level (optional, default "info"), --dry-run (optional, default False)
```

Two flags are now optional. The prose is no longer true.

## What evergreen says

```
[med] in_docs_not_code  README.md:15 — claims "all flags required"; cli.py:42-67 now has optional
                        --log-level and --dry-run → flag for review
```

## Flag, never rewrite

Evergreen does **not** generate the replacement sentence — it won't invent how you want to phrase it.
It points: *"line 15 says all flags are required; the code now has optional ones. Rewrite?"* The *what*
is proven; the *how* is yours.

## Why it matters

Rung 4 is the last and the most dangerous, because the rot hides in prose that still reads fluently.
It only runs after rungs 1–3 are clean (and the full pass only in `strict` mode), with the code in front of you.
