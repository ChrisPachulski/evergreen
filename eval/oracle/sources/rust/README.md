# Rust source candidates

`catalog.json` records 20 license-compatible public Rust repositories at exact commits and trees.
Each initial candidate binds one regular Git blob, its byte length and SHA-256, the repository
license bytes, and the canonical extracted-inventory digest. `generate.py` verifies those facts
from local Git object databases and derives the shell-free extraction command. It does not use a
working-tree file as evidence.

Prepare one local clone or bare mirror per catalog `source_id`, then verify without network access:

```sh
python3 -m eval.oracle.sources.rust.generate \
  --repositories /absolute/path/to/rust-source-mirrors
```

The command emits a canonical public-source inventory to standard output. A nonzero exit means at
least one origin, commit, tree, license, source blob, byte count, hash, or recipe binding did not
verify. Source verification uses the same object-database boundary as the shared verifier: Git
replacement objects and user/system configuration are disabled, the path must resolve to a regular
`100644` or `100755` blob, and the blob object ID, size, and SHA-256 must all match. The Rust catalog
shape is a candidate inventory, not a `provenance.json` source record; `provenance_record()` therefore
fails closed until a runnable adapter receipt exists.

`prototype-return-value.json` demonstrates the missing source-to-seed bridge without asserting a
label. It selects the exact `const INITIALIZING: usize = 1;` byte span from the pinned `rust-lang/log`
blob. `derive.py` rechecks the whole blob hash and span hash, accepts only the closed
`rust-const-usize-return-v1` grammar, deterministically builds a standalone wrapper and
documentation statement, and emits a receipt binding the generator bytes, source blob, span,
wrapper, documentation, oracle kind, and observed value. Run it only into an owner-only external
custody destination because standard output includes the private wrapper and documentation:

```sh
python3 -m eval.oracle.sources.rust.derive \
  --repository /absolute/path/to/rust-rust-lang-log
```

The prototype covers one real return-value candidate. It is not corpus capacity: the shared
contract still needs to admit derivation receipts, receipt the pinned Rust adapter execution, add
finite real-source operators for all five oracle kinds, and prevent structurally duplicate wrappers
from crossing splits.

This inventory is source capacity, not benchmark coverage. It contains no seed claims, labels,
mutation identities, private split information, harness receipt, sandbox-image receipt, or asserted
oracle-kind counts. Before any Rust seed is admissible, the external custodian must bind its exact
source code and oracle kind to a verified `source_blobs` member and execute all baseline, mutation,
and no-op variants through the receipted Rust adapter. The checked-in public provenance remains not
ready until that shared custody contract and the required real seed inventory pass.
