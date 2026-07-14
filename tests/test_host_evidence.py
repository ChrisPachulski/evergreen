import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HostEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "home with spaces"
        self.home.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_host_evidence_is_raw_read_only_and_complete_for_both_hosts(self):
        from evergreen.hosts import _block, collect_host_evidence, install

        for directory in (".claude", ".codex"):
            (self.home / directory).mkdir()
        self.assertTrue(install(self.home, ROOT, "all").ok)
        before = self.snapshot(include_directories=True)

        evidence = collect_host_evidence(self.home, ROOT, "all")

        self.assertEqual(self.snapshot(include_directories=True), before)
        self.assertEqual(set(evidence), {"schema_version", "kind", "canonical", "hosts"})
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["kind"], "evergreen-host-evidence")
        self.assertEqual(set(evidence["hosts"]), {"claude", "codex"})
        self.assertEqual(
            evidence["canonical"]["version"],
            json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"],
        )
        self.assertEqual(
            evidence["canonical"]["hashes"]["bin/evergreen"],
            hashlib.sha256((ROOT / "bin" / "evergreen").read_bytes()).hexdigest(),
        )

        def booleans(value):
            if type(value) is bool:
                yield value
            elif isinstance(value, dict):
                for item in value.values():
                    yield from booleans(item)
            elif isinstance(value, list):
                for item in value:
                    yield from booleans(item)

        self.assertEqual(list(booleans(evidence)), [])
        for name, directory, instruction in (
            ("claude", ".claude", "CLAUDE.md"),
            ("codex", ".codex", "AGENTS.md"),
        ):
            with self.subTest(host=name):
                host = evidence["hosts"][name]
                self.assertEqual(set(host), {
                    "lexical_root", "resolved_root", "resolution_chain", "ownership",
                    "installed", "doctor_issues", "discovery", "uninstall_owned_paths",
                })
                self.assertEqual(
                    set(host["installed"]["artifacts"]),
                    {"instructions", "ownership", "skill", "skills_parent"},
                )
                for artifact in host["installed"]["artifacts"].values():
                    self.assertEqual(
                        set(artifact), {"path", "kind", "sha256", "target", "uid", "mode"}
                    )
                self.assertEqual(host["doctor_issues"], [])
                self.assertEqual(host["installed"], host["discovery"])
                self.assertEqual(
                    host["ownership"]["sha256"],
                    hashlib.sha256(
                        (self.home / directory / ".evergreen-owned.json").read_bytes()
                    ).hexdigest(),
                )
                self.assertEqual(
                    host["installed"]["instruction_block_sha256"],
                    hashlib.sha256(_block(ROOT)).hexdigest(),
                )
                self.assertEqual(
                    set(host["uninstall_owned_paths"]), {
                        str(self.home / directory / instruction),
                        str(self.home / directory / "skills" / "evergreen"),
                        str(self.home / directory / ".evergreen-owned.json"),
                    },
                )

    def test_host_evidence_keeps_stale_claude_independent_from_codex(self):
        from evergreen.hosts import collect_host_evidence, install

        for directory in (".claude", ".codex"):
            (self.home / directory).mkdir()
        self.assertTrue(install(self.home, ROOT, "all").ok)
        stale = self.home / ".claude" / "skills" / "evergreen"
        stale.unlink()
        stale.symlink_to(self.home / "stale-cache")

        evidence = collect_host_evidence(self.home, ROOT, "all")

        self.assertIn("skill-link-stale", evidence["hosts"]["claude"]["doctor_issues"])
        self.assertEqual(evidence["hosts"]["codex"]["doctor_issues"], [])

    def test_host_evidence_rejects_symlinked_content_from_canonical_hashes(self):
        from evergreen.hosts import collect_host_evidence

        plugin = Path(self.temporary.name) / "canonical"
        shutil.copytree(ROOT, plugin, symlinks=True)
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (outside / "command.md").write_text("untrusted\n")
        (plugin / "commands" / "linked").symlink_to(outside, target_is_directory=True)

        evidence = collect_host_evidence(self.home, plugin, "all")

        self.assertEqual(evidence["canonical"]["hashes"], {})
        for host in evidence["hosts"].values():
            self.assertIn("canonical-invalid", host["doctor_issues"])

    def test_host_evidence_rejects_writable_canonical_command_from_hashes(self):
        from evergreen.hosts import collect_host_evidence

        plugin = Path(self.temporary.name) / "canonical"
        shutil.copytree(ROOT, plugin, symlinks=True)
        (plugin / "commands" / "impact.md").chmod(0o666)

        evidence = collect_host_evidence(self.home, plugin, "all")

        self.assertEqual(evidence["canonical"]["hashes"], {})
        for host in evidence["hosts"].values():
            self.assertIn("canonical-invalid", host["doctor_issues"])

    def test_complete_instruction_hash_detects_text_outside_owned_block_per_host(self):
        from evergreen.hosts import collect_host_evidence, install

        for directory in (".claude", ".codex"):
            (self.home / directory).mkdir()
        self.assertTrue(install(self.home, ROOT, "all").ok)
        before = collect_host_evidence(self.home, ROOT, "all")
        instructions = self.home / ".claude" / "CLAUDE.md"
        instructions.write_bytes(b"user text changed\n" + instructions.read_bytes())

        after = collect_host_evidence(self.home, ROOT, "all")

        self.assertNotEqual(before["hosts"]["claude"], after["hosts"]["claude"])
        self.assertEqual(before["hosts"]["codex"], after["hosts"]["codex"])
        self.assertEqual(
            after["hosts"]["claude"]["installed"]["artifacts"]["instructions"]["sha256"],
            hashlib.sha256(instructions.read_bytes()).hexdigest(),
        )

    def test_writable_instruction_file_prevents_only_claude_alignment(self):
        self.assert_mutable_claude_artifact_isolated(
            "instructions", 0o666, "instruction-file-unsafe"
        )

    def test_writable_ownership_file_prevents_only_claude_alignment(self):
        self.assert_mutable_claude_artifact_isolated(
            "ownership", 0o666, "ownership-file-unsafe"
        )

    def test_writable_skills_parent_prevents_only_claude_alignment(self):
        self.assert_mutable_claude_artifact_isolated(
            "skills_parent", 0o777, "skills-parent-unsafe"
        )

    def assert_mutable_claude_artifact_isolated(self, artifact, mode, issue):
        from evergreen.hosts import collect_host_evidence, host_evidence_aligned, install

        for directory in (".claude", ".codex"):
            (self.home / directory).mkdir()
        self.assertTrue(install(self.home, ROOT, "all").ok)
        paths = {
            "instructions": self.home / ".claude" / "CLAUDE.md",
            "ownership": self.home / ".claude" / ".evergreen-owned.json",
            "skills_parent": self.home / ".claude" / "skills",
        }
        paths[artifact].chmod(mode)

        evidence = collect_host_evidence(self.home, ROOT, "all")

        self.assertIn(issue, evidence["hosts"]["claude"]["doctor_issues"])
        self.assertFalse(host_evidence_aligned(evidence, "claude"))
        self.assertEqual(evidence["hosts"]["codex"]["doctor_issues"], [])
        self.assertTrue(host_evidence_aligned(evidence, "codex"))

    def snapshot(self, include_directories=False):
        values = {}
        for path in self.home.rglob("*"):
            if path.name == ".evergreen-host.lock":
                continue
            relative = path.relative_to(self.home).as_posix()
            if path.is_symlink():
                values[relative] = ("link", os.readlink(path))
            elif path.is_file():
                values[relative] = ("file", path.read_bytes())
            elif include_directories:
                values[relative] = ("directory",)
        return values


if __name__ == "__main__":
    unittest.main()
