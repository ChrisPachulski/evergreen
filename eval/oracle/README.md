# Executable-oracle source pack

This directory defines the public provenance and external-custody boundary for the planned
five-language executable-oracle corpus. No curated source identities or private oracle packages
are present in this checkout. The checked-in `provenance.json` therefore reports zero sources and
zero claims, and readiness validation fails closed.

The public contract requires at least 20 source-project groups and 250 seed claims per language
across Python, Java, TypeScript, Rust, and Go. Every language must cover all five oracle kinds with
at least 40 seeds and 20 projects per kind before splitting. Each public source record binds the
exact HTTPS origin, commit and tree, SPDX license and license-file hash, shell-free extraction
recipe and hash, a closed inventory of every seed blob's repository path, Git object ID, SHA-256,
extracted input path, and oracle kind, the adapter hash, digest-addressed sandbox image, and pinned
toolchain identity. The extracted-tree commitment is recomputed from that canonical blob inventory.
`sources/toolchain-policy-v1.json` freezes those identities and their exact trusted-CI
action commits and version variables; validation rejects drift between the policy, provenance, and
workflow. Public recipes live under `sources/<language>/` only after those facts have been
verified; none are fabricated as placeholders. Reused origins or projects must keep one lineage,
and duplicate source/content identities cannot be counted as independent capacity.

The missing deliverable is one owner-only external custody receipt conforming to
`private-custody-schema-v1.json`, plus the artifacts it commits to: the complete seed manifest,
split-key bytes, development package, holdout package, and executable/adapter toolchain receipts.
That receipt contains hashes and aggregate counts only. It never contains holdout code,
documentation, labels, mutation identities, or split key bytes, and it remains outside the
detector repository. The public manifest may contain commitments to those artifacts after they
exist, but never their paths or contents.

Custody validation opens those owner-only artifacts through bounded, no-symlink reads without
printing their rows. It requires four distinct files and an exact 32-byte split key, validates each
seed with the frozen oracle schema, requires its source path, bytes, hash, and oracle kind to match
exactly one public blob witness, derives the fixed harness command and expected
source/mutation/no-op rows, requires each
sealed package to be their exact closed-key, canonical JSONL serialization, recomputes the keyed
split and per-language/kind inventories, and compares those results to the public claims.
Self-consistent hashes or receipt totals alone are insufficient.

Public blob claims are independently recomputable from a local bare or working Git object database;
the verifier reads the pinned commit and tree, license bytes, regular-blob object IDs, and blob bytes
without checking out or executing repository content:

```sh
python3 -m eval.oracle.build verify-source-checkout \
  --manifest /path/to/provenance.json \
  --source-id SOURCE_ID \
  --repository /path/to/exact/git/object-database
```

Validate the checked-in, non-ready contract without network access:

```sh
python3 -m eval.oracle.build validate-provenance \
  --manifest eval/oracle/sources/provenance.json \
  --contract-only
```

The readiness command intentionally exits `2` until real source identities, public recipes, scale,
kind capacity, and custody commitments are present:

```sh
python3 -m eval.oracle.build validate-provenance \
  --manifest eval/oracle/sources/provenance.json
```

CI reruns the public contract on macOS and Linux after networked setup. A separate manual,
environment-protected Linux job selects the exact Python, Node/TypeScript, JDK, Rust, and Go
versions, applies offline proxy settings, and requires readiness before trusted regeneration can
begin. Unit tests separately refuse process and socket use during provenance validation. The
workflow does not claim that regeneration or either private package currently exists.
