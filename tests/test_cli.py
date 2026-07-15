import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest
import runpy


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "evergreen"
def is_inventory_path(path, mode):
    del path
    return mode in {"100644", "100755"}


class EvergreenCLITests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def run_cli(self, *args, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )

    @staticmethod
    def run_git(directory, *args):
        return subprocess.run(
            ["git", "-C", str(directory), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def make_git_repo(self):
        repo = Path(self.temporary.name) / "git-repo"
        repo.mkdir()
        self.run_git(repo, "init", "-q", "-b", "main")
        self.run_git(repo, "config", "user.email", "test@example.com")
        self.run_git(repo, "config", "user.name", "Test")
        (repo / "tracked").write_text("tracked\n")
        self.run_git(repo, "add", "tracked")
        self.run_git(repo, "commit", "-qm", "initial")
        return repo

    def make_grade_repositories(
        self, *, bootstrap=False, subject_files=None, subject_executable_files=None,
    ):
        from tests.test_grade import valid_evidence

        suffix = "-bootstrap" if bootstrap else ""
        verifier = Path(self.temporary.name) / f"trusted-verifier{suffix}"
        shutil.copytree(
            ROOT,
            verifier,
            ignore=shutil.ignore_patterns(
                ".git", ".superpowers", "__pycache__", "*.pyc"
            ),
        )
        self.run_git(verifier, "init", "-q", "-b", "main")
        self.run_git(verifier, "config", "user.email", "test@example.com")
        self.run_git(verifier, "config", "user.name", "Test")
        self.run_git(verifier, "add", ".")
        self.run_git(verifier, "commit", "-qm", "trusted verifier")
        verifier_commit = self.run_git(verifier, "rev-parse", "HEAD")

        candidate = Path(self.temporary.name) / f"grade-candidate{suffix}"
        self.run_git(Path(self.temporary.name), "clone", "-q", str(verifier), str(candidate))
        self.run_git(candidate, "config", "user.email", "test@example.com")
        self.run_git(candidate, "config", "user.name", "Test")
        if not bootstrap:
            (candidate / "subject-marker").write_text("candidate subject\n")
            for path, content in (subject_files or {}).items():
                target = candidate / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            for path, content in (subject_executable_files or {}).items():
                target = candidate / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(target.stat().st_mode | stat.S_IXUSR)
            self.run_git(candidate, "add", ".")
            self.run_git(candidate, "commit", "-qm", "candidate subject")
        subject_commit = self.run_git(candidate, "rev-parse", "HEAD")
        subject_tree = self.run_git(candidate, "rev-parse", "HEAD^{tree}")

        release = candidate / "eval" / "grade" / "public" / "0.5.0"
        release.mkdir(parents=True)
        policy = (candidate / "eval" / "grade-policy-v1.json").read_bytes()
        evidence = valid_evidence()
        evidence["subject"] = {"commit": subject_commit, "tree": subject_tree}
        evidence["policy"]["sha256"] = hashlib.sha256(policy).hexdigest()
        for counts in evidence["detector"].values():
            counts["subject_commit"] = subject_commit
        for result in evidence["peers"][0]["results"]:
            result["subject_commit"] = subject_commit
        inventory = []
        for record in self.run_git(candidate, "ls-tree", "-r", subject_commit).splitlines():
            metadata, path = record.split("\t", 1)
            mode, _kind, _object_id = metadata.split(" ")
            if is_inventory_path(path, mode):
                inventory.append(path)
        evidence["subject_executables"] = []
        for path in inventory:
            digest = hashlib.sha256((candidate / path).read_bytes()).hexdigest()
            evidence["subject_executables"].append({
                "path": path,
                "subject_sha256": digest,
                "evidence_sha256": digest,
            })
        manifest = release / "evidence.json"
        manifest.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n")
        (release / "policy.json").write_bytes(policy)
        (release / "report.md").write_text("# Evidence report\n")
        self.run_git(candidate, "add", "eval/grade/public/0.5.0")
        self.run_git(candidate, "commit", "-qm", "publish grade evidence")
        return verifier, candidate, verifier_commit, "eval/grade/public/0.5.0/evidence.json"

    @staticmethod
    def file_snapshot(root):
        return {
            path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        }

    def write_map(self):
        (self.repo / ".evergreen-map.json").write_text(json.dumps({
            "version": 1,
            "maps": [{"sources": ["src/**"], "docs": ["docs/api.md"]}],
        }))

    def evidence(self, **changes):
        value = {
            "provider": "shape", "version": "1", "type": "export-removed",
            "path": "src/client.py", "line": 1, "span": None,
            "symbol": "Client", "old": "present", "current": "missing",
            "confidence": "deterministic", "metadata": {},
        }
        value.update(changes)
        return value

    def snapshot(self):
        return {
            path.relative_to(self.repo).as_posix(): path.read_bytes()
            for path in self.repo.rglob("*") if path.is_file()
        }

    def test_help_and_usage_exit_codes(self):
        root_help = self.run_cli("--help")
        impact_help = self.run_cli("impact", "--help")
        receipt_help = self.run_cli("receipt", "--help")
        grade_help = self.run_cli("grade", "--help")
        verify_help = self.run_cli("grade", "verify", "--help")
        bad_usage = self.run_cli("unknown")
        missing_repo = self.run_cli("impact", "--repo", str(self.repo / "missing"), "a.py")

        self.assertEqual(root_help.returncode, 0)
        for command in ("impact", "receipt", "grade"):
            self.assertIn(command, root_help.stdout)
        self.assertEqual(impact_help.returncode, 0)
        for flag in ("--repo", "--evidence", "--json"):
            self.assertIn(flag, impact_help.stdout)
        self.assertEqual(receipt_help.returncode, 0)
        for flag in ("--repo", "--benchmark-manifest", "--json"):
            self.assertIn(flag, receipt_help.stdout)
        self.assertEqual(grade_help.returncode, 0)
        self.assertEqual(grade_help.stdout, (
            "usage: evergreen grade [-h] {verify} ...\n\n"
            "positional arguments:\n"
            "  {verify}\n"
            "    verify    verify evidence earned an A grade\n\n"
            "options:\n"
            "  -h, --help  show this help message and exit\n"
        ))
        self.assertEqual(verify_help.returncode, 0)
        self.assertEqual(verify_help.stdout, (
            "usage: evergreen grade verify [-h] --repo PATH --manifest PATH [--json]\n\n"
            "options:\n"
            "  -h, --help       show this help message and exit\n"
            "  --repo PATH      repository containing committed evidence\n"
            "  --manifest PATH  repository-relative evidence manifest\n"
            "  --json           emit one JSON object\n"
        ))
        self.assertEqual(bad_usage.returncode, 2)
        self.assertEqual(missing_repo.returncode, 2)
        self.assertIn("repository must be a directory", missing_repo.stderr)

        hostile_repo = "\x1b[31mbad\nrepo\x7f"
        hostile = self.run_cli("impact", "--repo", hostile_repo, "a.py")
        self.assertEqual(hostile.returncode, 2)
        self.assertNotIn("\x1b", hostile.stderr)
        self.assertEqual(hostile.stderr.count("\n"), 1)
        self.assertIn("\\x1b", hostile.stderr)
        self.assertIn("\\n", hostile.stderr)
        self.assertIn("\\x7f", hostile.stderr)

    def test_grade_verify_is_deterministic_read_only_and_human_json_agree(self):
        verifier, candidate, _commit, manifest = self.make_grade_repositories()
        script = verifier / "bin" / "evergreen"
        before_verifier = self.file_snapshot(verifier)
        before_candidate = self.file_snapshot(candidate)

        json_result = subprocess.run(
            [sys.executable, str(script), "grade", "verify", "--repo", str(candidate),
             "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        repeated = subprocess.run(
            [sys.executable, str(script), "grade", "verify", "--repo", str(candidate),
             "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        human = subprocess.run(
            [sys.executable, str(script), "grade", "verify", "--repo", str(candidate),
             "--manifest", manifest],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        self.assertEqual(json_result.returncode, 2, json_result.stderr)
        self.assertEqual(repeated.returncode, 2, repeated.stderr)
        self.assertEqual(human.returncode, 2, human.stderr)
        self.assertEqual(json_result.stdout, repeated.stdout)
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["status"], "not-earned")
        self.assertIsNone(payload["grade"])
        self.assertEqual(set(payload["verifier"]), {"commit", "tree", "artifact_sha256"})
        categories = {item["id"]: item for item in payload["categories"]}
        for category, reason in (
            ("same_corpus_comparison", "predicate:peer_applicability"),
            ("reproducibility_ci", "predicate:macos_linux"),
            ("cleanup", "predicate:clean_tree"),
        ):
            self.assertEqual(categories[category]["status"], "not-earned")
            self.assertIn(reason, categories[category]["reasons"])
        self.assertIn("status: not-earned", human.stdout)
        self.assertIn("grade: none", human.stdout)
        for category in payload["categories"]:
            for reason in category["reasons"]:
                self.assertIn(reason, human.stdout)
        self.assertEqual(before_verifier, self.file_snapshot(verifier))
        self.assertEqual(before_candidate, self.file_snapshot(candidate))

    def test_grade_verify_recomputes_claude_and_codex_independently_from_live_hosts(self):
        from evergreen.hosts import collect_host_evidence, install

        verifier, candidate, _commit, manifest = self.make_grade_repositories()
        home = Path(self.temporary.name) / "grade-host-home"
        (home / ".claude").mkdir(parents=True)
        (home / ".codex").mkdir()
        (home / ".claude" / "CLAUDE.md").write_text("original user text\n")
        self.assertTrue(install(home, candidate, "all").ok)
        evidence_path = candidate / manifest
        evidence = json.loads(evidence_path.read_text())
        evidence["host_evidence"] = collect_host_evidence(home, candidate, "all")
        evidence_path.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        self.run_git(candidate, "add", manifest)
        self.run_git(candidate, "commit", "-qm", "record raw host evidence")
        env = os.environ.copy()
        env["HOME"] = str(home)

        healthy = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        stale_link = home / ".claude" / "skills" / "evergreen"
        stale_link.unlink()
        stale_link.symlink_to(home / "stale-plugin-cache")
        stale = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        stale_link.unlink()
        stale_link.symlink_to(candidate.resolve() / "skills" / "evergreen")
        instructions = home / ".claude" / "CLAUDE.md"
        instructions.write_bytes(
            instructions.read_bytes().replace(b"original user text", b"changed user text", 1)
        )
        changed_instruction = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        self.assertEqual(healthy.returncode, 2, healthy.stderr)
        healthy_categories = {
            item["id"]: item for item in json.loads(healthy.stdout)["categories"]
        }
        self.assertEqual(healthy_categories["claude_self_application"]["status"], "earned")
        self.assertEqual(healthy_categories["codex_self_application"]["status"], "earned")
        self.assertEqual(stale.returncode, 2, stale.stderr)
        stale_payload = json.loads(stale.stdout)
        stale_categories = {item["id"]: item for item in stale_payload["categories"]}
        self.assertEqual(stale_categories["claude_self_application"]["status"], "not-earned")
        self.assertEqual(stale_categories["codex_self_application"]["status"], "earned")
        self.assertIn(
            "skill-link-stale",
            stale_payload["host_observation"]["hosts"]["claude"]["doctor_issues"],
        )
        changed_payload = json.loads(changed_instruction.stdout)
        changed_categories = {
            item["id"]: item
            for item in changed_payload["categories"]
        }
        self.assertEqual(
            changed_categories["claude_self_application"]["status"], "not-earned"
        )
        self.assertEqual(changed_categories["codex_self_application"]["status"], "earned")
        self.assertEqual(
            changed_payload["host_observation"]["hosts"]["claude"]["doctor_issues"], []
        )
        declared_hash = evidence["host_evidence"]["hosts"]["claude"]["installed"][
            "artifacts"
        ]["instructions"]["sha256"]
        live_hash = changed_payload["host_observation"]["hosts"]["claude"]["installed"][
            "artifacts"
        ]["instructions"]["sha256"]
        self.assertNotEqual(declared_hash, live_hash)

    def test_grade_verify_binds_public_policy_to_frozen_subject_policy(self):
        verifier, candidate, _commit, manifest = self.make_grade_repositories()
        policy_path = candidate / "eval" / "grade" / "public" / "0.5.0" / "policy.json"
        policy = json.loads(policy_path.read_text())
        policy["detector"]["thresholds"]["f1"] = 0.81
        policy_bytes = json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        policy_path.write_bytes(policy_bytes)
        evidence_path = candidate / manifest
        evidence = json.loads(evidence_path.read_text())
        evidence["policy"]["sha256"] = hashlib.sha256(policy_bytes).hexdigest()
        evidence_path.write_text(json.dumps(evidence, sort_keys=True, separators=(",", ":")))
        self.run_git(candidate, "add", "eval/grade/public/0.5.0")
        self.run_git(candidate, "commit", "-qm", "replace public policy after freeze")

        result = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "invalid")
        self.assertEqual(payload["failures"][0]["code"], "policy-subject-mismatch")

    def test_grade_verify_requires_exact_trusted_executable_inventory(self):
        verifier, candidate, _commit, manifest = self.make_grade_repositories()
        evidence_path = candidate / manifest
        complete = json.loads(evidence_path.read_text())

        missing = json.loads(json.dumps(complete))
        missing["subject_executables"].pop()
        evidence_path.write_text(json.dumps(missing, sort_keys=True, separators=(",", ":")))
        self.run_git(candidate, "add", manifest)
        self.run_git(candidate, "commit", "-qm", "omit required executable")
        missing_result = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        extra = json.loads(json.dumps(complete))
        extra["subject_executables"].append({
            "path": "not-in-subject.bin", "subject_sha256": "a" * 64,
            "evidence_sha256": "a" * 64,
        })
        extra["subject_executables"].sort(key=lambda item: item["path"])
        evidence_path.write_text(json.dumps(extra, sort_keys=True, separators=(",", ":")))
        self.run_git(candidate, "add", manifest)
        self.run_git(candidate, "commit", "-qm", "add candidate-selected executable")
        extra_result = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        for result in (missing_result, extra_result):
            self.assertEqual(result.returncode, 2)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "invalid")
            self.assertEqual(
                payload["failures"][0]["code"], "executable-inventory-mismatch"
            )

    def test_grade_verify_rejects_inventory_over_trusted_depth_limit(self):
        deep = "eval/oracle/" + "/".join(["nested"] * 14) + "/adapter.py"
        verifier, candidate, _commit, manifest = self.make_grade_repositories(
            subject_files={deep: b"# adapter\n"}
        )

        result = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "invalid")
        self.assertEqual(payload["failures"][0]["code"], "executable-inventory-limit")

    def test_grade_verify_bounds_hostile_expanduser_as_operational_result(self):
        verifier, candidate, _commit, manifest = self.make_grade_repositories()
        missing_user = "~evergreen_missing_user_7f8f1"
        command = [
            sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
            "--repo", missing_user, "--manifest", manifest,
        ]
        json_result = subprocess.run(
            [*command, "--json"], cwd=candidate,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        human_result = subprocess.run(
            command, cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        for result in (json_result, human_result):
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.stderr, "")
        payload = json.loads(json_result.stdout)
        self.assertEqual(payload["status"], "inconclusive")
        self.assertEqual(payload["failures"][0]["code"], "operational-error")
        self.assertEqual(set(payload["verifier"]), {"commit", "tree", "artifact_sha256"})
        self.assertIn("status: inconclusive", human_result.stdout)
        self.assertIn("operational-error", human_result.stdout)

    def test_grade_verify_reuses_one_expanded_repository_path_for_both_snapshots(self):
        verifier, candidate, _commit, manifest = self.make_grade_repositories()
        env = os.environ.copy()
        env["HOME"] = str(Path(self.temporary.name))

        result = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", "~/grade-candidate", "--manifest", manifest, "--json"],
            cwd=candidate, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "not-earned")
        self.assertNotEqual(payload.get("failures", [{}])[0].get("code"), "operational-error")

    def test_grade_inventory_covers_semantic_ci_hook_plugin_and_executable_inputs(self):
        verifier, candidate, _commit, manifest = self.make_grade_repositories(
            subject_files={"tools/unknown-config": b"semantic=true\n"},
            subject_executable_files={"tools/semantic-runner": b"#!/bin/sh\nexit 0\n"},
        )
        evidence = json.loads((candidate / manifest).read_text())
        declared = {item["path"] for item in evidence["subject_executables"]}

        self.assertTrue({
            "ci/path_policy.py",
            "hooks/hooks.json",
            "action.yml",
            "eval/run.sh",
            "eval/score.py",
            "tests/action.sh",
            ".claude-plugin/plugin.json",
            ".codex-plugin/plugin.json",
            ".github/workflows/test.yml",
            "commands/impact.md",
            "skills/evergreen/SKILL.md",
            "AGENTS.md",
            ".evergreen-ignore",
            ".gitignore",
            "eval/manifest.tsv",
            "eval/fixture/README.md",
            "eval/bench/dataset.jsonl",
            "tools/unknown-config",
            "tools/semantic-runner",
        }.issubset(declared))
        result = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "not-earned")

        evidence = json.loads((candidate / manifest).read_text())
        evidence["subject_executables"] = [
            item for item in evidence["subject_executables"]
            if item["path"] != "tools/unknown-config"
        ]
        (candidate / manifest).write_text(
            json.dumps(evidence, sort_keys=True, separators=(",", ":")) + "\n"
        )
        self.run_git(candidate, "add", manifest)
        self.run_git(candidate, "commit", "-qm", "omit unknown semantic input")
        omitted = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(omitted.returncode, 2)
        self.assertEqual(
            json.loads(omitted.stdout)["failures"][0]["code"],
            "executable-inventory-mismatch",
        )

    def test_grade_verify_deep_json_is_bounded_invalid_without_traceback(self):
        verifier, candidate, _commit, manifest = self.make_grade_repositories()
        nested = b'{"x":' * 1_000 + b"null" + b"}" * 1_000
        (candidate / manifest).write_bytes(nested)
        self.run_git(candidate, "add", manifest)
        self.run_git(candidate, "commit", "-qm", "hostile nested evidence")

        result = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        self.assertEqual(result.returncode, 2, (result.stdout, result.stderr))
        self.assertNotIn("Traceback", result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "invalid")
        self.assertEqual(payload["failures"][0]["code"], "invalid-evidence")
        self.assertIn("JSON structure exceeds trusted limits", payload["failures"][0]["detail"])

    def test_grade_verify_refuses_unsafe_files_dirty_state_and_bootstrap(self):
        verifier, candidate, _commit, manifest = self.make_grade_repositories()
        script = verifier / "bin" / "evergreen"

        def run(repo, supplied=manifest):
            return subprocess.run(
                [sys.executable, str(script), "grade", "verify", "--repo", str(repo),
                 "--manifest", supplied, "--json"],
                cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )

        traversal = run(candidate, "../evidence.json")
        (candidate / "dirty-file").write_text("dirty\n")
        dirty = run(candidate)
        (candidate / "dirty-file").unlink()
        evidence_path = candidate / manifest
        original = evidence_path.read_bytes()
        evidence_path.unlink()
        evidence_path.symlink_to("policy.json")
        self.run_git(candidate, "add", manifest)
        self.run_git(candidate, "commit", "-qm", "unsafe symlink evidence")
        symlink = run(candidate)
        evidence_path.unlink()
        evidence_path.write_bytes(original)
        self.run_git(candidate, "add", manifest)
        self.run_git(candidate, "commit", "-qm", "restore regular evidence")
        evidence_path.unlink()
        evidence_path.mkdir()
        nonregular = run(candidate)
        evidence_path.rmdir()
        evidence_path.write_bytes(original + b" ")
        non_head = run(candidate)

        bootstrap_verifier, bootstrap, _commit, bootstrap_manifest = (
            self.make_grade_repositories(bootstrap=True)
        )
        bootstrap_result = subprocess.run(
            [sys.executable, str(bootstrap_verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(bootstrap), "--manifest", bootstrap_manifest, "--json"],
            cwd=bootstrap, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        expected_codes = (
            (traversal, "manifest-path-invalid"),
            (dirty, "repository-dirty"),
            (symlink, "evidence-file-unsafe"),
            (nonregular, "repository-dirty"),
            (non_head, "repository-dirty"),
            (bootstrap_result, "verifier-bootstrap"),
        )
        for result, code in expected_codes:
            with self.subTest(code=code, stdout=result.stdout, stderr=result.stderr):
                self.assertEqual(result.returncode, 2)
                payload = json.loads(result.stdout)
                self.assertEqual(payload["status"], "invalid")
                self.assertIsNone(payload["grade"])
                self.assertEqual(payload["failures"][0]["code"], code)

    def test_grade_verify_refuses_oversized_committed_manifest(self):
        verifier, candidate, _commit, manifest = self.make_grade_repositories()
        evidence_path = candidate / manifest
        evidence_path.write_bytes(b"{" + b" " * 1_048_576 + b"}")
        self.run_git(candidate, "add", manifest)
        self.run_git(candidate, "commit", "-qm", "oversized evidence")

        result = subprocess.run(
            [sys.executable, str(verifier / "bin" / "evergreen"), "grade", "verify",
             "--repo", str(candidate), "--manifest", manifest, "--json"],
            cwd=candidate, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        self.assertEqual(result.returncode, 2)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "invalid")
        self.assertEqual(payload["failures"][0]["code"], "evidence-file-unsafe")

    def test_grade_verify_rejects_forbidden_control_and_command_flags(self):
        for flag in (
            "--award", "--export", "--threshold", "--skip", "--waive", "--command",
        ):
            with self.subTest(flag=flag):
                result = self.run_cli(
                    "grade", "verify", "--repo", ".", "--manifest", "evidence.json", flag
                )
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")

    def test_receipt_json_and_human_output_are_exact(self):
        git_repo = self.make_git_repo()

        result = self.run_cli("receipt", "--repo", str(git_repo), "--json")

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["repository"]["root"], str(git_repo.resolve()))
        self.assertEqual(
            result.stdout,
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ) + "\n",
        )

        human = self.run_cli("receipt", "--repo", str(git_repo))
        head = self.run_git(git_repo, "rev-parse", "HEAD")
        self.assertEqual(human.returncode, 0, human.stderr)
        self.assertEqual(human.stderr, "")
        self.assertEqual(human.stdout, (
            "Repository receipt:\n"
            f"- root: {git_repo.resolve()}\n"
            "- name: git-repo\n"
            "- origin: none\n"
            "- branch: main\n"
            f"- HEAD: {head}\n"
            "- upstream: none\n"
            "- ahead/behind: unknown\n"
            "- changes: staged=0 unstaged=0 untracked=0\n"
            "- clean: true\n"
            "Release evidence:\n"
            "- local tags at HEAD: none\n"
            "- external state: unverified\n"
            "Benchmark evidence:\n"
            "- none\n"
        ))

    def test_receipt_human_output_renders_every_benchmark_identity_field(self):
        namespace = runpy.run_path(str(SCRIPT), run_name="evergreen_cli_test")
        self.assertIn("print_receipt", namespace)
        output = io.StringIO()
        payload = {
            "schema_version": 1,
            "repository": {
                "root": "/repo",
                "name": "repo",
                "origin": None,
                "branch": None,
                "detached": True,
                "head": "a" * 40,
                "upstream": None,
                "ahead": None,
                "behind": None,
                "staged": 1,
                "unstaged": 2,
                "untracked": 3,
                "clean": False,
            },
            "release": {
                "local_tags": ["v1", "v2"],
                "external_state": "unverified",
            },
            "benchmark": {
                "artifact_count": 2,
                "evaluated_release": "0.4.0",
                "evidence_state": "declared_publication",
                "judge_sha256": "c" * 64,
                "languages": ["Python", "rust"],
                "manifest": "bench/manifest.json",
                "protocol": "java-git-window-v1",
                "provenance_commit": "b" * 40,
                "provider": "codex",
                "report": "bench/report.md",
                "resolver": "v2",
            },
        }

        with contextlib.redirect_stdout(output):
            namespace["print_receipt"](payload)

        self.assertEqual(output.getvalue(), (
            "Repository receipt:\n"
            "- root: /repo\n"
            "- name: repo\n"
            "- origin: none\n"
            "- branch: detached\n"
            f"- HEAD: {'a' * 40}\n"
            "- upstream: none\n"
            "- ahead/behind: unknown\n"
            "- changes: staged=1 unstaged=2 untracked=3\n"
            "- clean: false\n"
            "Release evidence:\n"
            "- local tags at HEAD: v1,v2\n"
            "- external state: unverified\n"
            "Benchmark evidence:\n"
            "- artifact count: 2\n"
            "- evaluated release: 0.4.0\n"
            "- evidence state: declared_publication\n"
            f"- judge SHA-256: {'c' * 64}\n"
            "- languages: Python,rust\n"
            "- manifest: bench/manifest.json\n"
            "- protocol: java-git-window-v1\n"
            f"- provenance commit: {'b' * 40}\n"
            "- provider: codex\n"
            "- report: bench/report.md\n"
            "- resolver: v2\n"
        ))

    def test_receipt_operational_git_failure_exits_one_with_safe_error(self):
        git_repo = self.make_git_repo()
        stub_dir = Path(self.temporary.name) / "stub-bin"
        stub_dir.mkdir()
        stub = stub_dir / "git"
        stub.write_text(
            f"#!{sys.executable}\n"
            "import sys, time\n"
            "sys.stdout.buffer.write(b'x' * 1048577)\n"
            "sys.stdout.buffer.flush()\n"
            "time.sleep(1)\n"
        )
        stub.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = str(stub_dir)

        result = self.run_cli(
            "receipt", "--repo", str(git_repo), "--json", env=env
        )

        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertLessEqual(len(result.stderr), 530)
        self.assertIn("too much output", result.stderr)

    def test_receipt_unborn_repository_is_invalid_input_exit_two(self):
        self.run_git(self.repo, "init", "-q", "-b", "main")

        without_index = self.run_cli("receipt", "--repo", str(self.repo), "--json")
        (self.repo / "staged").write_text("staged\n")
        self.run_git(self.repo, "add", "staged")
        with_index = self.run_cli("receipt", "--repo", str(self.repo), "--json")

        for result in (without_index, with_index):
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.stdout, "")
            self.assertNotIn("Traceback", result.stderr)

    def test_receipt_errors_are_single_terminal_safe_lines(self):
        git_repo = self.make_git_repo()
        invalid_repo = self.repo / "\x1b[31mbad\nrepo\x7f"
        invalid_manifest = "bad\x1b[2J\nmanifest.json"

        results = (
            self.run_cli("receipt", "--repo", str(invalid_repo)),
            self.run_cli(
                "receipt",
                "--repo",
                str(git_repo),
                "--benchmark-manifest",
                invalid_manifest,
            ),
        )

        for result in results:
            with self.subTest(stderr=result.stderr):
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertEqual(result.stderr.count("\n"), 1)
                self.assertNotIn("\x1b", result.stderr)
                self.assertNotIn("\x7f", result.stderr)
                self.assertLessEqual(len(result.stderr), 530)
                self.assertTrue(result.stderr.startswith("evergreen: "))

    def test_receipt_unresolved_repo_user_is_a_bounded_input_error(self):
        result = self.run_cli(
            "receipt", "--repo", "~evergreen_missing_user_7f8f1"
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertLessEqual(len(result.stderr), 530)
        self.assertTrue(result.stderr.startswith("evergreen: "))

    def test_receipt_unresolved_manifest_user_is_a_bounded_input_error(self):
        git_repo = self.make_git_repo()

        result = self.run_cli(
            "receipt",
            "--repo",
            str(git_repo),
            "--benchmark-manifest",
            "~evergreen_missing_user_7f8f1/manifest.json",
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr.count("\n"), 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertLessEqual(len(result.stderr), 530)
        self.assertTrue(result.stderr.startswith("evergreen: "))

    def test_receipt_does_not_import_posix_host_stack(self):
        git_repo = self.make_git_repo()
        script = f"""
import importlib.abc, runpy, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'fcntl' or fullname.startswith('evergreen.host'):
            raise ImportError('blocked host stack: ' + fullname)
sys.meta_path.insert(0, Block())
sys.argv = [{str(SCRIPT)!r}, 'receipt', '--json', '--repo', {str(git_repo)!r}]
runpy.run_path({str(SCRIPT)!r}, run_name='__main__')
"""

        result = subprocess.run(
            [sys.executable, "-c", script], cwd=self.repo,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            json.loads(result.stdout)["repository"]["root"],
            str(git_repo.resolve()),
        )

    def test_fresh_plugin_receipt_creates_no_bytecode_or_other_files(self):
        fresh = Path(self.temporary.name) / "fresh-receipt-plugin"
        shutil.copytree(
            ROOT,
            fresh,
            ignore=shutil.ignore_patterns(".git", ".superpowers", "__pycache__", "*.pyc"),
        )
        self.run_git(fresh, "init", "-q", "-b", "main")
        self.run_git(fresh, "config", "user.email", "test@example.com")
        self.run_git(fresh, "config", "user.name", "Test")
        self.run_git(fresh, "add", ".")
        self.run_git(fresh, "commit", "-qm", "initial")

        def tree_snapshot():
            return {
                path.relative_to(fresh).as_posix(): (
                    "directory" if path.is_dir()
                    else hashlib.sha256(path.read_bytes()).hexdigest()
                )
                for path in fresh.rglob("*")
            }

        before = tree_snapshot()
        result = subprocess.run(
            [
                sys.executable,
                str(fresh / "bin" / "evergreen"),
                "receipt",
                "--json",
                "--repo",
                str(fresh),
            ],
            cwd=fresh,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tree_snapshot(), before)
        self.assertFalse(any(path.name == "__pycache__" for path in fresh.rglob("*")))

    def test_candidate_cli_does_not_import_posix_host_stack(self):
        script = f"""
import importlib.abc, runpy, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'fcntl' or fullname.startswith('evergreen.host'):
            raise ImportError('blocked host stack: ' + fullname)
sys.meta_path.insert(0, Block())
sys.argv = [{str(SCRIPT)!r}, 'impact', '--json', '--repo', {str(self.repo)!r}, 'a.py']
runpy.run_path({str(SCRIPT)!r}, run_name='__main__')
"""
        result = subprocess.run(
            [sys.executable, "-c", script], cwd=self.repo,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["candidates"][0]["path"], "a.py")

    def test_host_commands_have_parallel_selection_and_dry_run_interfaces(self):
        root_help = self.run_cli("--help")
        install_help = self.run_cli("install", "--help")
        uninstall_help = self.run_cli("uninstall", "--help")
        doctor_help = self.run_cli("doctor", "--help")

        for command in ("install", "uninstall", "doctor"):
            self.assertIn(command, root_help.stdout)
        for result in (install_help, uninstall_help):
            self.assertEqual(result.returncode, 0)
            self.assertIn("--host", result.stdout)
            self.assertIn("--dry-run", result.stdout)
        self.assertIn("--host", doctor_help.stdout)
        self.assertIn("--repo", doctor_help.stdout)
        self.assertIn("--json", doctor_help.stdout)

    def test_host_cli_uses_fake_home_and_dry_run_and_uninstall_are_reversible(self):
        home = Path(self.temporary.name) / "fake home with spaces"
        (home / ".claude").mkdir(parents=True)
        (home / ".codex").mkdir()
        agents = home / ".codex" / "AGENTS.md"
        agents.write_text("user-owned\n")
        env = os.environ.copy()
        env["HOME"] = str(home)

        preview = self.run_cli("install", "--host", "all", "--dry-run", env=env)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertFalse((home / ".claude" / "CLAUDE.md").exists())
        self.assertEqual(agents.read_text(), "user-owned\n")

        installed = self.run_cli("install", "--host", "all", env=env)
        diagnosed = self.run_cli("doctor", "--host", "all", "--repo", str(ROOT), env=env)
        removed = self.run_cli("uninstall", "--host", "all", env=env)

        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(diagnosed.returncode, 0, diagnosed.stderr)
        self.assertIn("healthy", diagnosed.stdout.lower())
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(agents.read_text(), "user-owned\n")
        self.assertFalse((home / ".claude" / "skills" / "evergreen").is_symlink())
        self.assertFalse((home / ".codex" / "skills" / "evergreen").is_symlink())

    def test_doctor_json_emits_raw_host_evidence_without_shared_verdict(self):
        from evergreen.hosts import install

        home = Path(self.temporary.name) / "doctor-json-home"
        plugin = Path(self.temporary.name) / "doctor-json-plugin"
        shutil.copytree(
            ROOT, plugin,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (home / ".claude").mkdir(parents=True)
        (home / ".codex").mkdir()
        self.assertTrue(install(home, plugin, "all").ok)
        env = os.environ.copy()
        env["HOME"] = str(home)

        result = self.run_cli(
            "doctor", "--host", "all", "--repo", str(plugin), "--json", env=env
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["kind"], "evergreen-host-evidence")
        self.assertEqual(set(payload["hosts"]), {"claude", "codex"})
        serialized = result.stdout.lower()
        for forbidden in ('"ok"', '"pass"', '"passed"', '"grade"', '"success"'):
            self.assertNotIn(forbidden, serialized)

    def test_explicit_absent_host_refuses_without_creating_configuration(self):
        home = Path(self.temporary.name) / "empty-home"
        home.mkdir()
        env = os.environ.copy()
        env["HOME"] = str(home)

        result = self.run_cli("install", "--host", "codex", env=env)

        self.assertEqual(result.returncode, 2)
        self.assertIn("not detected", result.stderr)
        self.assertEqual(list(home.iterdir()), [])

    def test_human_output_is_candidate_only_and_does_not_mutate_project(self):
        self.write_map()
        before = self.snapshot()

        result = self.run_cli("impact", "--repo", str(self.repo), "src/client.py")

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertIn("Candidates (2):", result.stdout)
        self.assertIn("[100] docs/api.md", result.stdout)
        self.assertIn("[10] src/client.py", result.stdout)
        self.assertNotIn("finding", result.stdout.lower())
        self.assertNotIn("verdict", result.stdout.lower())
        self.assertEqual(self.snapshot(), before)

    def test_human_output_escapes_terminal_controls_from_untrusted_values(self):
        namespace = runpy.run_path(str(SCRIPT), run_name="evergreen_cli_test")
        output = io.StringIO()
        payload = {
            "candidates": [{
                "path": "hostile\npath\x1b[31m.py",
                "rank": 10,
                "reasons": ["reason\rwith\x7f controls"],
            }],
            "warnings": ["warning\nwith\x1b[2J controls"],
        }
        with contextlib.redirect_stdout(output):
            namespace["print_human"](payload)

        rendered = output.getvalue()
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x7f", rendered)
        for escaped in (r"\n", r"\r", r"\x1b", r"\x7f"):
            self.assertIn(escaped, rendered)

    def test_fresh_self_repo_query_creates_no_bytecode_or_other_files(self):
        fresh = Path(self.temporary.name) / "fresh-plugin"
        shutil.copytree(
            ROOT,
            fresh,
            ignore=shutil.ignore_patterns(".git", ".superpowers", "__pycache__", "*.pyc"),
        )

        def tree_snapshot():
            return {
                path.relative_to(fresh).as_posix(): (
                    "directory" if path.is_dir()
                    else hashlib.sha256(path.read_bytes()).hexdigest()
                )
                for path in fresh.rglob("*")
            }

        before = tree_snapshot()
        result = subprocess.run(
            [
                sys.executable,
                str(fresh / "bin" / "evergreen"),
                "impact",
                "--json",
                "--repo",
                str(fresh),
                "evergreen/impact.py",
            ],
            cwd=fresh,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(tree_snapshot(), before)
        self.assertFalse(any(path.name == "__pycache__" for path in fresh.rglob("*")))

    def test_json_output_and_warnings_are_stable_candidates(self):
        result = self.run_cli(
            "impact", "--json", "--repo", str(self.repo), "valid.py", "../escape.py"
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        payload = json.loads(result.stdout)
        self.assertEqual(set(payload), {"schema_version", "candidates", "warnings"})
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["candidates"], [{
            "path": "valid.py", "rank": 10, "reasons": ["changed path valid.py"],
        }])
        self.assertTrue(any("changed path" in warning for warning in payload["warnings"]))
        serialized = result.stdout.lower()
        self.assertNotIn("finding", serialized)
        self.assertNotIn("verdict", serialized)

    def test_missing_map_and_evidence_input_are_additive(self):
        evidence_path = self.repo / "evidence.json"
        evidence_path.write_text(json.dumps([self.evidence()]))

        missing_map = self.run_cli("impact", "plain.py")
        with_evidence = self.run_cli(
            "impact", "--json", "--evidence", str(evidence_path)
        )

        self.assertEqual(missing_map.returncode, 0)
        self.assertIn("[10] plain.py", missing_map.stdout)
        payload = json.loads(with_evidence.stdout)
        self.assertEqual([(item["path"], item["rank"]) for item in payload["candidates"]], [
            ("src/client.py", 50),
        ])
        self.assertEqual(payload["warnings"], [])

    def test_evidence_warnings_do_not_turn_candidate_query_into_failure(self):
        evidence_path = self.repo / "bad-evidence.json"
        evidence_path.write_text("not json")

        result = self.run_cli(
            "impact", "--json", "--evidence", str(evidence_path), "valid.py"
        )

        self.assertEqual(result.returncode, 0)
        payload = json.loads(result.stdout)
        self.assertEqual([item["path"] for item in payload["candidates"]], ["valid.py"])
        self.assertTrue(any("invalid JSON" in warning for warning in payload["warnings"]))

    def test_agent_commands_and_manifests_preserve_claude_discovery(self):
        claude_command = (ROOT / "commands" / "impact.md").read_text()
        codex_command = tomllib.loads((ROOT / "commands" / "impact.toml").read_text())
        claude_manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        codex_manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())

        for content in (claude_command, codex_command["prompt"]):
            self.assertIn("bin/evergreen", content)
            self.assertIn("--json", content)
            self.assertIn("candidate", content.lower())
            self.assertIn("Do not edit", content)
        self.assertEqual(claude_manifest["commands"], ["./commands/"])
        self.assertNotIn("commands", codex_manifest)
        self.assertTrue(any(
            "impact" in prompt.lower()
            for prompt in codex_manifest["interface"]["defaultPrompt"]
        ))

    def test_cultivate_frontmatter_description_is_a_parseable_quoted_scalar(self):
        command = (ROOT / "commands" / "cultivate.md").read_text()
        description_line = next(
            line for line in command.splitlines() if line.startswith("description:")
        )

        encoded = description_line.partition(":")[2].strip()
        self.assertTrue(encoded.startswith('"') and encoded.endswith('"'))
        description = json.loads(encoded)

        self.assertIn("Cultivate repo hygiene", description)
        self.assertIn("never auto, never \"clean\"", description)


if __name__ == "__main__":
    unittest.main()
