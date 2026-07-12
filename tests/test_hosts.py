import json
import os
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class HostTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "home with spaces"
        self.home.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def test_detect_hosts_reports_absent_and_present_in_stable_order(self):
        from evergreen.hosts import detect_hosts

        absent = detect_hosts(self.home)
        (self.home / ".codex").mkdir()
        present = detect_hosts(self.home)

        self.assertEqual([item.name for item in absent], ["claude", "codex"])
        self.assertEqual([item.present for item in absent], [False, False])
        self.assertEqual([item.present for item in present], [False, True])
        self.assertEqual(present[1].instructions, self.home / ".codex" / "AGENTS.md")
        self.assertEqual(present[1].skill, self.home / ".codex" / "skills" / "evergreen")

    def test_install_preserves_unmarked_instructions_and_is_idempotent_for_both_hosts(self):
        from evergreen.hosts import BEGIN_MARKER, END_MARKER, install

        for directory, filename in ((".claude", "CLAUDE.md"), (".codex", "AGENTS.md")):
            host = self.home / directory
            host.mkdir()
            (host / filename).write_text("user instructions\n")

        first = install(self.home, ROOT, "all")
        snapshot = self.snapshot()
        second = install(self.home, ROOT, "all")

        self.assertTrue(first.ok, first.messages)
        self.assertTrue(second.ok, second.messages)
        self.assertEqual(self.snapshot(), snapshot)
        for directory, filename in ((".claude", "CLAUDE.md"), (".codex", "AGENTS.md")):
            host = self.home / directory
            text = (host / filename).read_text()
            self.assertTrue(text.startswith("user instructions\n"))
            self.assertEqual(text.count(BEGIN_MARKER), 1)
            self.assertEqual(text.count(END_MARKER), 1)
            link = host / "skills" / "evergreen"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), (ROOT / "skills" / "evergreen").resolve())

    def test_install_refuses_unmarked_skill_content_and_malformed_owned_block(self):
        from evergreen.hosts import BEGIN_MARKER, install

        codex = self.home / ".codex"
        (codex / "skills" / "evergreen").mkdir(parents=True)
        instructions = codex / "AGENTS.md"
        instructions.write_text(f"keep me\n{BEGIN_MARKER}\nunterminated\n")
        before = self.snapshot()

        result = install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(any("refusing" in message for message in result.messages))

    def test_reversed_markers_and_instruction_symlink_are_refused_without_following(self):
        from evergreen.hosts import BEGIN_MARKER, END_MARKER, install

        claude = self.home / ".claude"
        codex = self.home / ".codex"
        claude.mkdir()
        codex.mkdir()
        (claude / "CLAUDE.md").write_text(f"{END_MARKER}\ntext\n{BEGIN_MARKER}\n")
        outside = self.home / "outside instructions"
        outside.write_text("untouched\n")
        (codex / "AGENTS.md").symlink_to(outside)
        before = self.snapshot()

        reversed_result = install(self.home, ROOT, "claude")
        symlink_result = install(self.home, ROOT, "codex")

        self.assertFalse(reversed_result.ok)
        self.assertFalse(symlink_result.ok)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(outside.read_text(), "untouched\n")

    def test_broken_instruction_symlink_is_refused_without_creating_its_target(self):
        from evergreen.hosts import install

        codex = self.home / ".codex"
        codex.mkdir()
        missing = self.home / "must stay missing"
        (codex / "AGENTS.md").symlink_to(missing)

        result = install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertFalse(missing.exists())
        self.assertTrue((codex / "AGENTS.md").is_symlink())

    def test_broken_skill_link_is_diagnosed_then_repaired(self):
        from evergreen.hosts import doctor, install

        claude = self.home / ".claude"
        (claude / "skills").mkdir(parents=True)
        (claude / "skills" / "evergreen").symlink_to(self.home / "missing skill")

        unhealthy = doctor(self.home, ROOT, "claude")
        repaired = install(self.home, ROOT, "claude")
        healthy = doctor(self.home, ROOT, "claude")

        self.assertFalse(unhealthy.ok)
        self.assertTrue(any("broken" in message for message in unhealthy.messages))
        self.assertTrue(repaired.ok, repaired.messages)
        self.assertTrue(healthy.ok, healthy.messages)

    def test_dry_run_describes_changes_without_touching_home_with_spaces(self):
        from evergreen.hosts import install

        (self.home / ".claude").mkdir()
        before = self.snapshot(include_directories=True)

        result = install(self.home, ROOT, "claude", dry_run=True)

        self.assertTrue(result.ok, result.messages)
        self.assertTrue(any("would" in message for message in result.messages))
        self.assertEqual(self.snapshot(include_directories=True), before)

    def test_uninstall_removes_only_owned_state_and_preserves_user_text(self):
        from evergreen.hosts import install, uninstall

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_text("keep before\nkeep after")
        self.assertTrue(install(self.home, ROOT, "codex").ok)

        removed = uninstall(self.home, "codex")
        repeated = uninstall(self.home, "codex")

        self.assertTrue(removed.ok, removed.messages)
        self.assertTrue(repeated.ok, repeated.messages)
        self.assertEqual(instructions.read_text(), "keep before\nkeep after")
        self.assertFalse((codex / "skills" / "evergreen").exists())
        self.assertFalse((codex / "skills" / "evergreen").is_symlink())

    def test_uninstall_dry_run_and_unmarked_instructions_are_non_mutating(self):
        from evergreen.hosts import uninstall

        claude = self.home / ".claude"
        claude.mkdir()
        instructions = claude / "CLAUDE.md"
        instructions.write_text("unmarked\n")
        before = self.snapshot(include_directories=True)

        result = uninstall(self.home, "claude", dry_run=True)

        self.assertTrue(result.ok, result.messages)
        self.assertEqual(self.snapshot(include_directories=True), before)
        self.assertEqual(instructions.read_text(), "unmarked\n")

    def test_doctor_checks_canonical_version_manifests_rules_command_and_stale_block(self):
        from evergreen.hosts import doctor, install

        (self.home / ".codex").mkdir()
        self.assertTrue(install(self.home, ROOT, "codex").ok)
        healthy = doctor(self.home, ROOT, "codex")
        instructions = self.home / ".codex" / "AGENTS.md"
        instructions.write_text(instructions.read_text().replace(str(ROOT), "/stale/plugin"))
        stale = doctor(self.home, ROOT, "codex")

        versions = {
            json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"],
            json.loads((ROOT / ".codex-plugin" / "plugin.json").read_text())["version"],
        }
        self.assertEqual(len(versions), 1)
        self.assertTrue(healthy.ok, healthy.messages)
        for token in ("version", "manifests", "rules", "command", "codex"):
            self.assertTrue(any(token in message.lower() for message in healthy.messages))
        self.assertFalse(stale.ok)
        self.assertTrue(any("stale" in message for message in stale.messages))

    def test_doctor_reports_malformed_manifest_shapes_without_raising(self):
        from evergreen.hosts import doctor

        plugin = Path(self.temporary.name) / "plugin with spaces"
        (plugin / ".claude-plugin").mkdir(parents=True)
        (plugin / ".codex-plugin").mkdir()
        (plugin / ".claude-plugin" / "plugin.json").write_text("[]")
        (plugin / ".codex-plugin" / "plugin.json").write_text("{}")
        (self.home / ".claude").mkdir()

        result = doctor(self.home, plugin, "claude")

        self.assertFalse(result.ok)
        self.assertTrue(any("manifests" in message for message in result.messages))

    def snapshot(self, include_directories=False):
        values = {}
        for path in self.home.rglob("*"):
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
