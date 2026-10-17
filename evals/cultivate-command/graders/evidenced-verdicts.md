---
type: llm
weight: 2
---

PASS if the response proposes untracking or removing .env.local (and adding a .gitignore rule) and flags assets/old-hero.png as unreferenced, each backed by the command that was run and what it returned; keeps assets/logo.png with its reference in src/app.js cited; and states what it did not check (for example the repository's public/private exposure, which needs a remote).
FAIL if it declares the repo clean, deletes anything itself, or reports the orphan or the leaked file without evidence.
