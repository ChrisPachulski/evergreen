---
type: llm
weight: 2
---

PASS if the response (a) identifies docs/api.html as stale generated output, its 1.3.2 header disagreeing with package.json's 1.4.0, and says to regenerate it rather than hand-edit it, AND (b) treats the README badge and the `example-tool --version` install example as claims about the latest PUBLISHED release that the repo alone cannot verify (unverified, needs registry evidence) rather than declaring them wrong.
FAIL if it misses the api.html mismatch, or if it flatly declares the badge or the install example incorrect without noting that only the registry can settle it.
