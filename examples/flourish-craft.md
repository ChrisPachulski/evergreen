# Flourishing an accurate-but-ugly README (the craft axis)

Not drift — the doc is *true*, just unreadable. `flourish` restructures it to the gold standard,
then verifies every claim still holds. The trap this example exists to kill: **"it's accurate"
reads as "leave it." It isn't.** An accurate README can still be the reason nobody understands
your project.

## The before — an ARCHITECTURE.md wearing a README's name

````md
# my-streaming-app

A self-hosted streaming platform. Members sign in, browse the library, watch live and
on-demand, and request new titles; the owner curates and administers...

## Architecture
Four runtimes, one product:
- Web client (`src/`) — React 19 + Vite SPA...
- Backend (`server/`) — Hono + TypeScript...

## Authentication
There is no homegrown password store. Identity comes from three providers...

## Backend surface (`/api`)
Everything the SPA needs hangs off `/api`...
````

Accurate, careful — and a monstrosity: **architecture-first** ordering, value prop buried in a
paragraph, no hero, no badges, no feature list, and — for a product with a UI — **no screenshot**.
A maintainer wrote it for maintainers. A visitor bounces.

## What flourish does

1. **Read the project first** — the manifest (name, scripts), the real feature set, and the repo
   for a logo/screenshot (`public/`, `assets/`, `.github/`). You can't write a hero or a feature
   list from the old README alone.
2. **Impose the skeleton** — force the visitor-facing top, demote the engineering detail below it.

## The after

````md
<div align="center">
<img src="public/logo.svg" alt="My Streaming App" width="88" />

# My Streaming App
**A self-hosted streaming platform your household owns end to end.**

![web](https://img.shields.io/badge/web-React_19-61dafb?style=flat-square)
![api](https://img.shields.io/badge/api-Hono-f59e0b?style=flat-square)
![license](https://img.shields.io/badge/license-MIT-111111?style=flat-square)
</div>

<!-- screenshot: capture the library view and add it here at a tracked path —
     public/screenshot.png (docs/ is often gitignored → dead link). Invisible marker only. -->

## Features
- **You own the box** — self-hosted; household signals never leave your hardware.
- **Live + on-demand** — an IPTV core alongside a scanned, metadata-rich library.
- **Three ways in** — Plex OAuth, Sign in with Apple, and WebAuthn passkeys, one allowlist.

## Quick start
```sh
npm install && npm run dev
```

## Architecture
Four runtimes, one product: <!-- the SAME accurate detail — now below the fold -->
- **Web client** (`src/`) — React 19 + Vite SPA...
````

## Why it matters

Every claim is still code-backed — the verify pass proves the rewrite the same way the reflex
proves a flag. What changed is **order and shape**: hero → value prop → badges → features → quick
start *first*, architecture *after*; walls of prose become scannable bullets; a missing screenshot
becomes an invisible marker at a tracked path — never a visible "screenshot goes here" box, never a
dead link. flourish is the one sanctioned prose-rewrite, and
**"already accurate" is never "already done"** — run it until the result passes the monstrosity
test (first screenful is hero → value → features → quick start, not internals).
