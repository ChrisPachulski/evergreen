import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
import runpy


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "evergreen"


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
        bad_usage = self.run_cli("unknown")
        missing_repo = self.run_cli("impact", "--repo", str(self.repo / "missing"), "a.py")

        self.assertEqual(root_help.returncode, 0)
        for command in ("impact", "receipt"):
            self.assertIn(command, root_help.stdout)
        self.assertEqual(impact_help.returncode, 0)
        for flag in ("--repo", "--evidence", "--json"):
            self.assertIn(flag, impact_help.stdout)
        self.assertEqual(receipt_help.returncode, 0)
        for flag in ("--repo", "--benchmark-manifest", "--json"):
            self.assertIn(flag, receipt_help.stdout)
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

        result = self.run_cli("receipt", "--repo", str(self.repo), "--json")

        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("no index", result.stderr)
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
