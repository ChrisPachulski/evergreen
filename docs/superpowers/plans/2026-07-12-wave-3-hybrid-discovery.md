# Wave 3 Hybrid Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve candidate recall with optional deterministic evidence and source-to-doc mappings without letting either become a truth verdict.

**Architecture:** A dependency-free `bin/evergreen` CLI validates passive evidence files and map configuration, then emits candidate impact records. The skill consumes candidates through the existing proof ladder. No repository config executes commands.

**Tech Stack:** Python 3 standard library, JSON, prompt command files.

## Global Constraints

- Provider files are supplied by a trusted caller; repository configuration never names executable commands.
- Provider evidence and impact maps expand/rank candidates only.
- Invalid provider/map records produce warnings and do not suppress normal grep discovery.
- No bundled language parser and no new dependency.

---

### Task 1: Provider evidence protocol

**Files:**
- Create: `evergreen/evidence.py`
- Create: `evergreen/__init__.py`
- Create: `schemas/evidence-provider-v1.schema.json`
- Create: `tests/test_evidence.py`

**Interfaces:**
- `load_evidence(path: Path, repo: Path) -> tuple[list[Evidence], list[str]]`.
- `Evidence` contains provider/version/type/path/line/span/symbol/old/current/confidence/metadata.

- [ ] Write failing tests for valid deterministic/advisory facts, malformed records, traversal, symlink escape, invalid lines/types/confidence, duplicate records, and bounded metadata.
- [ ] Implement immutable dataclass validation and deterministic deduplication.
- [ ] Run focused tests and commit with `feat: accept passive evidence providers`.

### Task 2: Source-to-doc maps and impact engine

**Files:**
- Create: `evergreen/impact.py`
- Create: `schemas/evergreen-map-v1.schema.json`
- Create: `tests/test_impact.py`
- Create: `examples/evergreen-map.json`

**Interfaces:**
- `load_map(repo: Path) -> tuple[list[ImpactMap], list[str]]` reads `.evergreen-map.json`.
- `impact(repo: Path, paths: list[str], evidence: list[Evidence]) -> ImpactReport`.

- [ ] Write failing tests for mapped/unmapped candidates, multiple maps, invalid patterns, deleted paths, provider candidates, stable ordering, and the rule that mappings never suppress discovery.
- [ ] Implement JSON map parsing with `fnmatch`, repository-relative normalization, and reasons per candidate.
- [ ] Run focused tests and commit with `feat: map source changes to living docs`.

### Task 3: `evergreen impact` CLI and agent commands

**Files:**
- Create: `bin/evergreen`
- Create: `commands/impact.md`
- Create: `commands/impact.toml`
- Create: `tests/test_cli.py`
- Modify: `.claude-plugin/plugin.json`
- Modify: `.codex-plugin/plugin.json`

**Interfaces:**
- `bin/evergreen impact [--repo PATH] [--evidence FILE] PATH...`.
- Human output and `--json` output both label records as candidates, never findings.

- [ ] Write failing CLI tests for help, human/JSON output, warnings, missing config, evidence input, and exit codes.
- [ ] Implement the standard-library CLI with no package installation requirement.
- [ ] Add Claude and Codex command surfaces that invoke/interpret the same impact contract.
- [ ] Run focused and full tests; commit with `feat: add pre-edit documentation impact query`.

### Task 4: Proof-boundary fixtures and documentation

**Files:**
- Create: `examples/provider-evidence.json`
- Create: `eval/fixture/docs/provider-boundary.md`
- Modify: `skills/evergreen/SKILL.md`
- Modify: `skills/evergreen/DIGEST.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/DESIGN.md`
- Modify: `tests/hooks.sh`

- [ ] Add fixtures where deterministic facts correctly seed a finding and where they tempt a semantic false positive that must be rejected.
- [ ] Document evidence-provider and map trust boundaries plus Drift-shaped interoperability.
- [ ] Assert Claude/Codex rule agreement for “candidate, not verdict.”
- [ ] Run all tests and commit with `docs: define evergreen hybrid evidence boundary`.
