import json
import os
from pathlib import Path
import shutil
import subprocess
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

    def set_test_xattr(self, path, suffix, value):
        if hasattr(os, "setxattr"):
            name = f"user.{suffix}"
            try:
                os.setxattr(path, name, value)
            except OSError:
                return None
            return name
        tool = shutil.which("xattr")
        if tool:
            name = f"com.evergreen.{suffix}"
            result = subprocess.run(
                [tool, "-w", name, value.decode("ascii"), str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            return name if result.returncode == 0 else None
        return None

    def get_test_xattr(self, path, name):
        if hasattr(os, "getxattr"):
            return os.getxattr(path, name)
        result = subprocess.run(
            [shutil.which("xattr"), "-p", name, str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        return result.stdout.rstrip(b"\n")

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

    def test_uninstall_replacement_link_refuses_all_hosts_without_any_mutation(self):
        from evergreen.hosts import install, uninstall

        claude = self.home / ".claude"
        codex = self.home / ".codex"
        claude.mkdir()
        codex.mkdir()
        (claude / "CLAUDE.md").write_bytes(b"claude bytes\r\n")
        (codex / "AGENTS.md").write_bytes(b"codex bytes\n")
        self.assertTrue(install(self.home, ROOT, "all").ok)
        link = codex / "skills" / "evergreen"
        replacement = self.home / "user replacement"
        replacement.mkdir()
        link.unlink()
        link.symlink_to(replacement, target_is_directory=True)
        before = self.snapshot(include_directories=True)

        result = uninstall(self.home, "all")

        self.assertFalse(result.ok)
        self.assertEqual(self.snapshot(include_directories=True), before)
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(replacement))
        self.assertTrue(any("replacement skill link" in message for message in result.messages))

    def test_uninstall_removes_owned_relative_skill_link_after_target_normalization(self):
        from evergreen.hosts import install, uninstall

        codex = self.home / ".codex"
        codex.mkdir()
        self.assertTrue(install(self.home, ROOT, "codex").ok)
        link = codex / "skills" / "evergreen"
        target = Path(os.path.relpath(
            ROOT / "skills" / "evergreen", link.parent.resolve()
        ))
        link.unlink()
        link.symlink_to(target, target_is_directory=True)

        result = uninstall(self.home, "codex")

        self.assertTrue(result.ok, result.messages)
        self.assertFalse(link.is_symlink())

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

    def test_install_refuses_sparse_instruction_larger_than_documented_limit(self):
        from evergreen.hosts import MAX_INSTRUCTION_BYTES, install

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        with instructions.open("wb") as stream:
            stream.seek(MAX_INSTRUCTION_BYTES)
            stream.write(b"x")
        before = instructions.stat()

        result = install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertEqual(instructions.stat().st_size, before.st_size)
        self.assertFalse((codex / "skills").exists())
        self.assertTrue(any("instruction byte limit" in message for message in result.messages))

    def test_ancestor_path_aliases_are_canonicalized_for_install_and_doctor(self):
        from evergreen.hosts import doctor, install

        real = Path(self.temporary.name) / "real root"
        plugin = real / "plugin"
        shutil.copytree(ROOT, plugin, symlinks=True)
        alias = Path(self.temporary.name) / "alias"
        alias.symlink_to(real, target_is_directory=True)
        aliased_plugin = alias / "plugin"
        (self.home / ".codex").mkdir()

        installed = install(self.home, aliased_plugin, "codex")
        checked = doctor(self.home, aliased_plugin, "codex")

        self.assertTrue(installed.ok, installed.messages)
        self.assertTrue(checked.ok, checked.messages)
        state = json.loads((self.home / ".codex" / ".evergreen-owned.json").read_text())
        self.assertEqual(state["plugin_root"], str(plugin.resolve()))

    def test_doctor_static_validation_rejects_invalid_python(self):
        from evergreen import hosts

        plugin = Path(self.temporary.name) / "broken plugin"
        shutil.copytree(ROOT, plugin, symlinks=True)
        command = plugin / "bin" / "evergreen"
        command.write_text("#!/usr/bin/env python3\nthis is invalid python !\n")
        command.chmod(0o755)
        (self.home / ".codex").mkdir()
        self.assertTrue(hosts.install(self.home, plugin, "codex").ok)

        result = hosts.doctor(self.home, plugin, "codex")

        self.assertFalse(result.ok)
        self.assertTrue(any("static validation failed" in message for message in result.messages))

    def test_regular_reader_requires_nonblocking_open_and_post_open_identity(self):
        from evergreen import hosts

        path = self.home / "bounded"
        path.write_bytes(b"safe")
        with mock.patch.object(hosts.os, "O_NONBLOCK", None, create=True):
            with self.assertRaisesRegex(OSError, "nonblocking"):
                hosts._read_regular_bounded(path, 10, "test file")

    def test_doctor_static_validation_never_executes_command_code(self):
        from evergreen import hosts

        plugin = Path(self.temporary.name) / "static plugin"
        shutil.copytree(ROOT, plugin, symlinks=True)
        marker = plugin / "command-ran"
        command = plugin / "bin" / "evergreen"
        command.write_text(
            "#!/usr/bin/env python3\n"
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('ran')\n"
        )
        command.chmod(0o755)
        (self.home / ".codex").mkdir()
        self.assertTrue(hosts.install(self.home, plugin, "codex").ok)

        result = hosts.doctor(self.home, plugin, "codex")

        self.assertTrue(result.ok, result.messages)
        self.assertFalse(marker.exists())

    def test_static_command_validation_requires_utf8_python_shebang(self):
        from evergreen import hosts

        command = self.home / "evergreen"
        for content, expected in (
            (b"print('missing shebang')\n", "Python shebang"),
            (b"#!/usr/bin/env python3\n\xff\n", "UTF-8"),
            (
                b"#!/usr/bin/env python3\n" + b"#" * hosts.MAX_COMMAND_BYTES,
                "byte limit",
            ),
        ):
            with self.subTest(expected=expected):
                command.write_bytes(content)
                self.assertIn(expected, hosts._validate_python_command(command))

    def test_static_command_shebang_accepts_only_exact_python_interpreters(self):
        from evergreen import hosts

        command = self.home / "evergreen"
        accepted = (
            "/usr/bin/env python",
            "/usr/bin/env python3",
            "/usr/bin/env python3.12",
            "/usr/bin/python",
            "/usr/local/bin/python3",
            "/opt/python3.12",
        )
        rejected = (
            "/usr/bin/notpython",
            "/usr/bin/pythonista",
            "/usr/bin/python2",
            "/bin/env python3",
            "/usr/bin/env notpython3",
            "/usr/bin/env python3-evil",
            "/usr/bin/env -S python3 -I",
            "/usr/bin/env python3.12.1",
            "/usr/bin/env python3.1234",
            "/usr/bin/env python3.001",
            "python3",
        )

        for interpreter in accepted:
            with self.subTest(accepted=interpreter):
                command.write_text(f"#!{interpreter}\npass\n")
                self.assertIsNone(hosts._validate_python_command(command))
        for interpreter in rejected:
            with self.subTest(rejected=interpreter):
                command.write_text(f"#!{interpreter}\npass\n")
                error = hosts._validate_python_command(command)
                self.assertIsNotNone(error)
                self.assertIn("Python shebang", error)

    def test_static_command_rejects_crlf_and_control_character_shebangs(self):
        from evergreen import hosts

        command = self.home / "evergreen"
        for content in (
            b"#!/usr/bin/env python3\r\npass\n",
            b"#!/usr/bin/env\tpython3\npass\n",
            b"#!/usr/bin/python3\x7f\npass\n",
        ):
            with self.subTest(content=content):
                command.write_bytes(content)
                error = hosts._validate_python_command(command)
                self.assertIsNotNone(error)
                self.assertIn("Python shebang", error)

    def test_readme_documents_runtime_platform_and_host_safety_bounds(self):
        from evergreen.hosts import MAX_INSTRUCTION_BYTES

        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("Python 3.10+", readme)
        self.assertIn("macOS and Linux", readme)
        self.assertIn("POSIX", readme)
        self.assertIn(f"{MAX_INSTRUCTION_BYTES // (1024 * 1024)} MiB", readme)

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
