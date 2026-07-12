import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


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
        from evergreen.hosts import BEGIN_MARKER, END_MARKER, MAX_STATE_BYTES, OWNERSHIP_FILE, install

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
            state = host / OWNERSHIP_FILE
            self.assertTrue(state.is_file())
            self.assertLessEqual(state.stat().st_size, MAX_STATE_BYTES)
            self.assertEqual(json.loads(state.read_text())["host"], directory.removeprefix("."))

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
        claude.mkdir()
        self.assertTrue(install(self.home, ROOT, "claude").ok)
        link = claude / "skills" / "evergreen"
        link.unlink()
        link.symlink_to(self.home / "missing skill")

        unhealthy = doctor(self.home, ROOT, "claude")
        repaired = install(self.home, ROOT, "claude")
        healthy = doctor(self.home, ROOT, "claude")

        self.assertFalse(unhealthy.ok)
        self.assertTrue(any("broken" in message for message in unhealthy.messages))
        self.assertTrue(repaired.ok, repaired.messages)
        self.assertTrue(healthy.ok, healthy.messages)

    def test_arbitrary_skill_link_without_owned_record_is_never_repaired_or_removed(self):
        from evergreen.hosts import install, uninstall

        codex = self.home / ".codex"
        (codex / "skills").mkdir(parents=True)
        link = codex / "skills" / "evergreen"
        link.symlink_to(self.home / "arbitrary target")
        before = self.snapshot(include_directories=True)

        attempted_install = install(self.home, ROOT, "codex")
        attempted_uninstall = uninstall(self.home, "codex")

        self.assertFalse(attempted_install.ok)
        self.assertFalse(attempted_uninstall.ok)
        self.assertEqual(self.snapshot(include_directories=True), before)

    def test_instruction_lifecycle_restores_absent_empty_and_no_newline_exactly(self):
        from evergreen.hosts import install, uninstall

        cases = (
            ("absent", None), ("empty", b""), ("no-newline", b"user bytes"),
            ("crlf", b"user\r\nbytes\r\n"), ("non-utf8", b"user-\xff-bytes"),
        )
        for name, original in cases:
            with self.subTest(name=name):
                home = self.home / name
                codex = home / ".codex"
                codex.mkdir(parents=True)
                instructions = codex / "AGENTS.md"
                if original is not None:
                    instructions.write_bytes(original)

                self.assertTrue(install(home, ROOT, "codex").ok)
                self.assertTrue(uninstall(home, "codex").ok)

                if original is None:
                    self.assertFalse(instructions.exists())
                else:
                    self.assertTrue(instructions.exists())
                    self.assertEqual(instructions.read_bytes(), original)

    def test_symlinked_or_non_directory_home_host_and_skills_components_are_refused(self):
        from evergreen.hosts import install

        real_home = self.home / "real"
        (real_home / ".claude").mkdir(parents=True)
        home_link = self.home / "linked-home"
        home_link.symlink_to(real_home, target_is_directory=True)
        self.assertFalse(install(home_link, ROOT, "claude").ok)

        host_link_home = self.home / "host-link"
        host_link_home.mkdir()
        (host_link_home / ".claude").symlink_to(
            real_home / ".claude", target_is_directory=True
        )
        self.assertFalse(install(host_link_home, ROOT, "claude").ok)

        host_file_home = self.home / "host-file"
        host_file_home.mkdir()
        (host_file_home / ".codex").write_text("not a directory")
        self.assertFalse(install(host_file_home, ROOT, "codex").ok)

        skills_link_home = self.home / "skills-link"
        (skills_link_home / ".codex").mkdir(parents=True)
        outside = self.home / "outside skills"
        outside.mkdir()
        (skills_link_home / ".codex" / "skills").symlink_to(
            outside, target_is_directory=True
        )
        before = self.snapshot(include_directories=True)
        self.assertFalse(install(skills_link_home, ROOT, "codex").ok)
        self.assertEqual(self.snapshot(include_directories=True), before)

    def test_all_host_preflight_is_atomic_when_second_host_is_unsafe(self):
        from evergreen.hosts import install

        (self.home / ".claude").mkdir()
        codex = self.home / ".codex"
        codex.mkdir()
        (codex / "skills").write_text("not a directory")
        before = self.snapshot(include_directories=True)

        result = install(self.home, ROOT, "all")

        self.assertFalse(result.ok)
        self.assertEqual(self.snapshot(include_directories=True), before)
        self.assertFalse((self.home / ".claude" / "CLAUDE.md").exists())

    def test_invalid_canonical_plugin_refuses_real_and_dry_run_before_home_planning(self):
        from evergreen.hosts import install

        (self.home / ".claude").mkdir()
        invalid = Path(self.temporary.name) / "invalid plugin"
        invalid.mkdir()
        before = self.snapshot(include_directories=True)

        preview = install(self.home, invalid, "claude", dry_run=True)
        real = install(self.home, invalid, "claude")

        self.assertFalse(preview.ok)
        self.assertFalse(real.ok)
        self.assertEqual(self.snapshot(include_directories=True), before)
        self.assertTrue(any("canonical" in message for message in preview.messages))

    def test_symlinked_canonical_root_and_owned_record_are_refused(self):
        from evergreen.hosts import OWNERSHIP_FILE, install, uninstall

        codex = self.home / ".codex"
        codex.mkdir()
        linked_plugin = Path(self.temporary.name) / "linked plugin"
        linked_plugin.symlink_to(ROOT, target_is_directory=True)
        before = self.snapshot(include_directories=True)
        self.assertFalse(install(self.home, linked_plugin, "codex").ok)
        self.assertEqual(self.snapshot(include_directories=True), before)

        outside = self.home / "outside ownership"
        outside.write_text("{}")
        (codex / OWNERSHIP_FILE).symlink_to(outside)
        before = self.snapshot(include_directories=True)
        self.assertFalse(install(self.home, ROOT, "codex").ok)
        self.assertFalse(uninstall(self.home, "codex").ok)
        self.assertEqual(self.snapshot(include_directories=True), before)
        self.assertEqual(outside.read_text(), "{}")

    def test_malformed_or_oversized_owned_record_refuses_all_mutation(self):
        from evergreen.hosts import MAX_STATE_BYTES, OWNERSHIP_FILE, install, uninstall

        codex = self.home / ".codex"
        codex.mkdir()
        state = codex / OWNERSHIP_FILE
        state.write_bytes(b"{" + b"x" * MAX_STATE_BYTES + b"}")
        before = self.snapshot(include_directories=True)

        attempted_install = install(self.home, ROOT, "codex")
        attempted_uninstall = uninstall(self.home, "codex")

        self.assertFalse(attempted_install.ok)
        self.assertFalse(attempted_uninstall.ok)
        self.assertEqual(self.snapshot(include_directories=True), before)

    def test_hard_linked_instruction_and_ownership_files_are_refused(self):
        from evergreen.hosts import OWNERSHIP_FILE, install, uninstall

        for name, relative in (("instructions", "AGENTS.md"), ("ownership", OWNERSHIP_FILE)):
            with self.subTest(name=name):
                home = self.home / f"hard-link-{name}"
                codex = home / ".codex"
                codex.mkdir(parents=True)
                outside = self.home / f"outside-{name}"
                outside.write_bytes(b"outside bytes")
                os.link(outside, codex / relative)
                before = self.snapshot(include_directories=True)

                attempted_install = install(home, ROOT, "codex")
                attempted_uninstall = uninstall(home, "codex")

                self.assertFalse(attempted_install.ok)
                self.assertFalse(attempted_uninstall.ok)
                self.assertTrue(any(
                    "hard link" in message
                    for message in attempted_install.messages + attempted_uninstall.messages
                ))
                self.assertEqual(self.snapshot(include_directories=True), before)
                self.assertEqual(outside.read_bytes(), b"outside bytes")

    def test_failure_at_every_action_boundary_rolls_back_both_hosts_exactly(self):
        from evergreen import hosts

        cases = [("before-first", 1, False)] + [
            (f"after-{boundary}", boundary, True) for boundary in range(1, 7)
        ]
        for name, boundary, after in cases:
            with self.subTest(name=name):
                home = self.home / name
                (home / ".claude").mkdir(parents=True)
                (home / ".codex").mkdir()
                (home / ".claude" / "CLAUDE.md").write_bytes(b"claude bytes")
                (home / ".codex" / "AGENTS.md").write_bytes(b"codex\r\nbytes\r\n")
                before = self.snapshot(include_directories=True)
                real_action = hosts._perform_action
                calls = 0

                def fail_at_boundary(action, path, value):
                    nonlocal calls
                    calls += 1
                    if calls == boundary and not after:
                        raise OSError(f"injected before action {calls}")
                    real_action(action, path, value)
                    if calls == boundary and after:
                        raise OSError(f"injected after action {calls}")

                with mock.patch.object(hosts, "_perform_action", side_effect=fail_at_boundary):
                    result = hosts.install(home, ROOT, "all")

                self.assertFalse(result.ok)
                self.assertIn("rolled back", " ".join(result.messages))
                self.assertEqual(self.snapshot(include_directories=True), before)

    def test_rollback_failure_reports_manual_recovery_and_never_success(self):
        from evergreen import hosts

        home = self.home / "rollback failure"
        (home / ".claude").mkdir(parents=True)
        real_action = hosts._perform_action

        def mutate_then_fail(action, path, value):
            real_action(action, path, value)
            raise OSError("injected action failure")

        with mock.patch.object(hosts, "_perform_action", side_effect=mutate_then_fail), \
             mock.patch.object(hosts, "_restore_snapshot", side_effect=OSError("rollback failed")):
            result = hosts.install(home, ROOT, "claude")

        self.assertFalse(result.ok)
        rendered = " ".join(result.messages).lower()
        self.assertIn("manual recovery", rendered)
        self.assertIn("rollback failed", rendered)
        self.assertNotIn("healthy", rendered)

    def test_uninstall_failure_at_every_action_boundary_restores_installed_state(self):
        from evergreen import hosts

        for boundary in range(1, 7):
            with self.subTest(boundary=boundary):
                home = self.home / f"uninstall-after-{boundary}"
                (home / ".claude").mkdir(parents=True)
                (home / ".codex").mkdir()
                (home / ".claude" / "CLAUDE.md").write_bytes(b"claude")
                (home / ".codex" / "AGENTS.md").write_bytes(b"codex")
                self.assertTrue(hosts.install(home, ROOT, "all").ok)
                before = self.snapshot(include_directories=True)
                real_action = hosts._perform_action
                calls = 0

                def fail_after_action(action, path, value):
                    nonlocal calls
                    calls += 1
                    real_action(action, path, value)
                    if calls == boundary:
                        raise OSError(f"injected after uninstall action {calls}")

                with mock.patch.object(hosts, "_perform_action", side_effect=fail_after_action):
                    result = hosts.uninstall(home, "all")

                self.assertFalse(result.ok)
                self.assertIn("rolled back", " ".join(result.messages))
                self.assertEqual(self.snapshot(include_directories=True), before)

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
        before_doctor = self.snapshot(include_directories=True)
        healthy = doctor(self.home, ROOT, "codex")
        self.assertEqual(self.snapshot(include_directories=True), before_doctor)
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
