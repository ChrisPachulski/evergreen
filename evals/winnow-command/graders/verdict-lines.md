---
type: llm
---

PASS if the response reports findings verdict-first as one line per finding carrying a severity, a category such as in_docs_not_code, a file:line in the doc, and the code it cites, AND states a claim count (an N of documented claims enumerated from a shown command) with every claim ending as certified, drift, or unverified.
FAIL if findings are prose paragraphs without file:line citations, if no claim count is stated, or if the response proposes editing CHANGELOG.md or docs/adr/0001-worker-pool.md.
