# Drifted code snippet (rung 3 — drifted snippet/signature)

A code example in the docs that no longer matches the source it claims to describe.

## The drift

README.md:28 shows:

    const result = processData(items, { verbose: true, timeout: 5000 });

src/index.ts:89 now declares:

    export function processData(
      items: Item[],
      options: ProcessOptions & { retries?: number }
    ): Promise<Result>

`timeout` was removed; `retries` was added. The snippet is stale.

## What evergreen says

```
[med] name_mismatch  README.md:28 — snippet passes `timeout` (removed) and omits `retries`
                     (added) vs src/index.ts:89 → flag for review
```

## Flag, don't auto-rewrite

The example needs a human call: is it showing *typical* usage (update it) or *legacy* usage (add a
deprecation note)? Evergreen reads both sides, cites the mismatch, and stops — it won't guess intent.

## Why it matters

Rung 3 is read-both-and-compare. The snippet looked authoritative and was quietly wrong; a reader
copies it and hits a type error.
