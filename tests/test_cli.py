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
        bad_usage = self.run_cli("unknown")
        missing_repo = self.run_cli("impact", "--repo", str(self.repo / "missing"), "a.py")

        self.assertEqual(root_help.returncode, 0)
        self.assertIn("impact", root_help.stdout)
        self.assertEqual(impact_help.returncode, 0)
        for flag in ("--repo", "--evidence", "--json"):
            self.assertIn(flag, impact_help.stdout)
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

    def test_agent_commands_and_manifests_register_one_candidate_contract(self):
        claude_command = (ROOT / "commands" / "impact.md").read_text()
        codex_command = tomllib.loads((ROOT / "commands" / "impact.toml").read_text())
        claude_manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        codex_manifest = json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())

        for content in (claude_command, codex_command["prompt"]):
            self.assertIn("bin/evergreen", content)
            self.assertIn("--json", content)
            self.assertIn("candidate", content.lower())
            self.assertIn("Do not edit", content)
        self.assertEqual(claude_manifest["commands"], ["./commands/impact.md"])
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
