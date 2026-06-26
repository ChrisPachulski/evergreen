# Exempt case — an ADR frozen in time (correctly left alone)

The discipline that keeps evergreen trusted: knowing what *not* to flag.

## The apparent drift

```
docs/adr/0003-sync-strategy.md:22   "We use fetch-on-demand for config sync."
src/config-sync.ts:9                const strategy = "polling";
```

Code and doc disagree — looks like drift.

## What evergreen says

```
left alone: docs/adr/0003-sync-strategy.md records a past decision (an ADR) — not a claim about
            current behavior. Not drift.
```

## Why it's exempt

ADRs *lead or freeze* code: they record *why we chose what we chose*, at a moment in time. This ADR
truthfully says we once chose fetch-on-demand — and a later ADR can record the move to polling. The
file isn't lying about *now*; it's a historical record. Specs, RFCs, roadmaps, and CHANGELOG history
are exempt for the same reason.

## When it would NOT be exempt

If `0003` were meant to describe *current* behavior (a "how sync works today" doc), or said "we will
*always* use fetch-on-demand" (a live constraint), then the mismatch is real drift.

## Why it matters

Flagging a frozen doc as stale teaches the agent to cry wolf — and a checker that cries wolf gets
muted, taking the real findings down with it.
