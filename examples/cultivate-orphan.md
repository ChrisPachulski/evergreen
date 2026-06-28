# An orphan the index-only pass can't see (hygiene · reference graph)

The chaff cultivate exists to find is rarely *named* like chaff. It's usually a real file whose
reason to exist got deleted — and the only thing that sees it is the reference graph, never a
filename grep.

## The miss — what a lazy pass does

```
git ls-files | grep -E 'AUDIT|SUMMARY|SYNTHESIS'   → nothing
git check-ignore <tracked files>                    → nothing
verdict: "no slop, repo clean"
```

Both hints came back empty, so it rubber-stamped "clean." It never asked the only question that
finds real chaff: **what's committed that nothing points to?** — and it never looked past the git
index at the files sitting on disk.

## The catch — the reference graph (mandatory)

```
public/gem.webm     6.4 MB   git grep gem.webm   → 0 code refs (only DESIGN.md mentions it)
src/.../Beacon.tsx     —      git ls-files Beacon → gone (mount dropped in commit faf29ca)
```

The component that played `gem.webm` was deleted; the 6.4 MB asset was left behind — committed to a
**public** repo. **Orphan.** Contrast `public/kraken.mp4`: `git grep` → 2 live refs → **kept,
cited**. Same file type, opposite verdict, because the evidence differs.

## Prove-or-drop — both directions

- `gem.webm` → **remove** — zero refs *and* its consumer was deleted (cite the commit). Look first:
  `DESIGN.md` claims the asset "stays in the tree," which is now stale → fix that line too.
- `recommender/research/` → **keep** — but you do **not** get to *guess* it's intentional. Read its
  local `.gitignore`: "the repo may be public… never commit `cache/`." The owner published it on
  purpose. Cited, not assumed — certifying-as-fine needs proof exactly like flagging does.

## Why it matters

A filename grep only finds chaff that announces itself, and almost none does. The reference graph
finds the 6.4 MB nobody references and the dead component nobody imports. And prove-or-drop forbids
*both* "looks like clutter" and "looks intentional" — you looked, with a citation, or it isn't a
verdict. "Clean" is not an output cultivate is allowed to produce.
