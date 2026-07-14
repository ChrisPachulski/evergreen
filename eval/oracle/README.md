# Executable-oracle source pack

This directory defines the public provenance and external-custody boundary for the planned
five-language executable-oracle corpus. No curated source identities or private oracle packages
are present in this checkout. The checked-in `provenance.json` therefore reports zero sources and
zero claims, and readiness validation fails closed.

The public contract requires at least 20 source-project groups and 250 seed claims per language
across Python, Java, TypeScript, Rust, and Go. Every language must cover all five oracle kinds with
at least 40 seeds and 20 projects per kind before splitting. Each public source record binds the
exact HTTPS origin, commit and tree, SPDX license and license-file hash, shell-free extraction
recipe and hash, fixed harness and hash, digest-addressed sandbox image, and pinned toolchain
identity. Public recipes live under `sources/<language>/` only after those facts have been verified;
none are fabricated as placeholders.

The missing deliverable is one owner-only external custody receipt conforming to
`private-custody-schema-v1.json`, plus the artifacts it commits to: the complete seed manifest,
split-key bytes, development package, holdout package, and executable/adapter toolchain receipts.
That receipt contains hashes and aggregate counts only. It never contains holdout code,
documentation, labels, mutation identities, or split key bytes, and it remains outside the
detector repository. The public manifest may contain commitments to those artifacts after they
exist, but never their paths or contents.

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
