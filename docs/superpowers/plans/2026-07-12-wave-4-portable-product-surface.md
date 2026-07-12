# Wave 4 Portable Product Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Claude and Codex equivalent install/doctor behavior, formalize safe executable verification, and extend release identity to packages and linked documentation surfaces.

**Architecture:** The existing dependency-free CLI gains host-aware install, doctor, dry-run, and uninstall commands using reversible marked blocks or links. Canonical skill/digest/AGENTS rules share the same safe-execution and generalized-release vocabulary, enforced by tests.

**Tech Stack:** Python 3 standard library, Claude/Codex plugin manifests, Markdown skill surfaces.

## Global Constraints

- Never overwrite unmarked user instructions or configuration.
- Every mutating CLI operation supports `--dry-run`; uninstall removes only Evergreen-owned state.
- Doctor does not modify a project.
- Prove-by-test never runs deployment, publication, portal, privileged, or destructive commands.
- External release state is unverified unless queried directly and linked to the same product.

---

### Task 1: Host-aware installer and reversible ownership

**Files:**
- Create: `evergreen/hosts.py`
- Create: `tests/test_hosts.py`
- Modify: `bin/evergreen`
- Modify: `tests/test_cli.py`

**Interfaces:**
- `detect_hosts(home: Path) -> list[HostStatus]`.
- CLI: `install --host claude|codex|all --dry-run`, `uninstall`, and `doctor`.

- [ ] Write failing tests in isolated fake homes for absent/present hosts, existing unmarked AGENTS content, repeated install, broken links, dry-run, uninstall, and path names with spaces.
- [ ] Implement reversible installation using Evergreen-owned marked blocks or symlinks where supported; refuse ambiguous existing state rather than overwrite it.
- [ ] Implement doctor checks for canonical version, manifests, rules, command availability, and stale/broken ownership.
- [ ] Run focused tests and commit with `feat: add reversible Claude and Codex setup`.

### Task 2: Safe prove-by-test contract

**Files:**
- Create: `evergreen/execution_policy.py`
- Create: `tests/test_execution_policy.py`
- Modify: `skills/evergreen/SKILL.md`
- Modify: `skills/evergreen/DIGEST.md`
- Modify: `AGENTS.md`
- Modify: `commands/winnow.md`

**Interfaces:**
- `classify_command(argv: list[str]) -> allowed | refused | inconclusive` for helper/doctor use.
- Skill rules remain authoritative for model-led execution.

- [ ] Write failing policy tests for repository-native test commands, timeouts, network/secret declarations, shell metacharacters, deployment/upload/push/portal commands, privilege escalation, cleanup, and unavailable isolation.
- [ ] Implement the minimal deny-by-default dangerous-operation classifier without pretending to be a shell parser.
- [ ] Update all agent surfaces with identical timeout, scratch, secret, network, refusal, and inconclusive semantics.
- [ ] Run focused tests and commit with `feat: bound executable documentation verification`.

### Task 3: Generalized release identity

**Files:**
- Modify: `skills/evergreen/SKILL.md`
- Modify: `skills/evergreen/DIGEST.md`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `docs/DESIGN.md`
- Modify: `tests/hooks.sh`
- Create: `examples/package-release-identity.md`

- [ ] Add failing cross-surface agreement tests for package manifests, registry versions, CLI output, badges, installed-command examples, generated API docs, deployed docs labels, independent streams, and external-state-unverified behavior.
- [ ] Generalize the release reflex while preserving Apple marketing/build rules and authorization boundaries.
- [ ] Add a fixture based on package/docs version mismatch without naming a competitor.
- [ ] Run focused tests and commit with `feat: extend release identity beyond apps`.

### Task 4: Product documentation, release manifests, and complete verification

**Files:**
- Modify: `README.md`
- Modify: `.claude-plugin/plugin.json`
- Modify: `.codex-plugin/plugin.json`
- Modify: `.claude-plugin/marketplace.json`
- Modify: `docs/DESIGN.md`

- [ ] Document one-command local use, Claude/Codex install/doctor/uninstall, trust boundaries, evidence providers, impact maps, CI status semantics, safe execution, evaluation coverage, and non-goals.
- [ ] Audit the completed diff under Evergreen's release policy; choose the justified pre-1.0 minor version and update all manifests together.
- [ ] Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash tests/action.sh
bash tests/hooks.sh
python3 eval/bench/run_bench.py --selftest
python3 bin/evergreen doctor --repo .
git diff --check
```

- [ ] Run a strict documentation freshness pass over README, design, skill, digest, AGENTS, commands, examples, and manifests.
- [ ] Commit with `release: ship best-in-class hybrid evergreen`.
