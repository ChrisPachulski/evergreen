# Human label rubric v1

Judge only the documentation claim against the shown code. A direct contradiction or a documented
behavior the code does not deliver is `inconsistent`. Use `direct-mismatch` for contradictory facts
and `over-promise` when the code fails to deliver a documented behavior. Code doing more than the
documentation mentions is `consistent`; under-promise is informational, not drift.

If a claim depends on an unseen callee, enclosing type, referenced constant, or other missing
context, choose `insufficient-context` and name the missing evidence. Never guess.

Every judgment records the precise documentation claim, code evidence, and a short rationale.
Inconsistent judgments also require a category. Work independently, without model assistance or
discussion with another annotator. Use only the opaque packet ID and do not seek the benchmark's
existing label or model outcome. Annotator IDs are pseudonyms; do not record personal information.

Examples:

- `direct-mismatch`: documentation says the function returns `true`, while every shown return path
  returns `false`.
- `over-promise`: documentation promises retries after a transient failure, while the shown code
  attempts the operation once and immediately returns the error.
- `consistent`: documentation promises a value is cached, and the shown code both reads and writes
  that cache; an additional undocumented metrics call does not make the claim inconsistent.
- `insufficient-context`: documentation promises validation, but the shown function delegates to an
  unseen validator whose behavior cannot be established from the packet.

Two humans label every item. A third human independently labels every disagreement, every
insufficient-context response, and a deterministic 10% sample of agreements. Automation accepts a
final label only when two decisive judgments match; otherwise the row remains unresolved.
