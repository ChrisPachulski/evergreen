import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
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

    def test_all_host_preflight_rejects_unsafe_second_host_before_mutation(self):
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

    def test_install_aborts_before_mutation_when_planned_instructions_change(self):
        from evergreen import hosts

        for directory, filename in ((".claude", "CLAUDE.md"), (".codex", "AGENTS.md")):
            root = self.home / directory
            root.mkdir()
            (root / filename).write_bytes(directory.encode())
        changed = self.home / ".codex" / "AGENTS.md"
        real_verify = hosts._verify_preflight

        def change_then_verify(captured):
            changed.write_bytes(b"concurrent replacement")
            return real_verify(captured)

        with mock.patch.object(hosts, "_verify_preflight", side_effect=change_then_verify):
            result = hosts.install(self.home, ROOT, "all")

        self.assertFalse(result.ok)
        self.assertEqual(changed.read_bytes(), b"concurrent replacement")
        self.assertFalse((self.home / ".claude" / "skills").exists())
        self.assertFalse((self.home / ".codex" / "skills").exists())

    def test_install_rejects_same_bytes_replacement_inode_after_planning(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"same bytes")
        original_inode = instructions.lstat().st_ino
        real_verify = hosts._verify_preflight

        def replace_then_verify(captured):
            instructions.unlink()
            instructions.write_bytes(b"same bytes")
            self.assertNotEqual(instructions.lstat().st_ino, original_inode)
            return real_verify(captured)

        with mock.patch.object(hosts, "_verify_preflight", side_effect=replace_then_verify):
            result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertEqual(instructions.read_bytes(), b"same bytes")
        self.assertFalse((codex / "skills").exists())

    def test_install_planning_consumes_captured_bytes_not_transient_live_reads(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")

        with mock.patch.object(hosts, "_read_instruction", return_value=b"transient"):
            result = hosts.install(self.home, ROOT, "codex")

        self.assertTrue(result.ok, result.messages)
        self.assertTrue(instructions.read_bytes().startswith(b"original\n"))
        self.assertNotIn(b"transient", instructions.read_bytes())

    def test_install_rejects_same_mode_skills_directory_replacement(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        skills = codex / "skills"
        skills.mkdir(parents=True)
        replaced = codex / "skills-replaced"
        real_verify = hosts._verify_preflight

        def replace_then_verify(captured):
            skills.rename(replaced)
            skills.mkdir(mode=0o755)
            return real_verify(captured)

        with mock.patch.object(hosts, "_verify_preflight", side_effect=replace_then_verify):
            result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertFalse((skills / "evergreen").exists())
        self.assertFalse((replaced / "evergreen").exists())

    def test_inner_mutation_recheck_preserves_edit_after_outer_verification(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        real_action = hosts._perform_action

        def edit_then_act(action, path, value, *args, **kwargs):
            if path.name == instructions.name:
                instructions.write_bytes(b"concurrent edit")
            return real_action(action, path, value, *args, **kwargs)

        with mock.patch.object(hosts, "_perform_action", side_effect=edit_then_act):
            result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertEqual(instructions.read_bytes(), b"concurrent edit")
        rendered = " ".join(result.messages).lower()
        self.assertIn("manual recovery", rendered)
        self.assertIn("exclusive access", rendered)

    def test_mkdir_eexist_never_adopts_or_rolls_back_concurrent_directory(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        skills = codex / "skills"
        real_mkdir = hosts.os.mkdir

        def concurrent_mkdir(path, mode=0o777, *, dir_fd=None):
            real_mkdir(path, mode, dir_fd=dir_fd)
            real_mkdir(path, mode, dir_fd=dir_fd)

        with mock.patch.object(hosts.os, "mkdir", side_effect=concurrent_mkdir):
            result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertTrue(skills.is_dir())
        self.assertFalse((skills / "evergreen").exists())
        self.assertIn("manual recovery", " ".join(result.messages).lower())

    def test_uninstall_aborts_before_mutation_when_owned_link_is_replaced(self):
        from evergreen import hosts

        (self.home / ".claude").mkdir()
        (self.home / ".codex").mkdir()
        self.assertTrue(hosts.install(self.home, ROOT, "all").ok)
        link = self.home / ".codex" / "skills" / "evergreen"
        replacement = self.home / "replacement"
        replacement.mkdir()
        real_verify = hosts._verify_preflight

        def replace_then_verify(captured):
            link.unlink()
            link.symlink_to(replacement, target_is_directory=True)
            return real_verify(captured)

        before_claude = self.snapshot(include_directories=True)
        with mock.patch.object(hosts, "_verify_preflight", side_effect=replace_then_verify):
            result = hosts.uninstall(self.home, "all")

        self.assertFalse(result.ok)
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), str(replacement))
        after = self.snapshot(include_directories=True)
        for path, value in before_claude.items():
            if not path.startswith(".codex/skills/evergreen"):
                self.assertEqual(after.get(path), value)

    def test_mid_apply_replacement_is_preserved_while_prior_actions_roll_back(self):
        from evergreen import hosts

        for directory, filename in ((".claude", "CLAUDE.md"), (".codex", "AGENTS.md")):
            root = self.home / directory
            root.mkdir()
            (root / filename).write_bytes(directory.encode())
        before = self.snapshot(include_directories=True)
        changed = self.home / ".codex" / "AGENTS.md"
        real_action = hosts._perform_action
        calls = 0

        def replace_future_target(action, path, value, *args, **kwargs):
            nonlocal calls
            calls += 1
            postimage = real_action(action, path, value, *args, **kwargs)
            if calls == 1:
                changed.write_bytes(b"concurrent replacement")
            return postimage

        with mock.patch.object(hosts, "_perform_action", side_effect=replace_future_target):
            result = hosts.install(self.home, ROOT, "all")

        self.assertFalse(result.ok)
        self.assertEqual(changed.read_bytes(), b"concurrent replacement")
        after = self.snapshot(include_directories=True)
        for path, value in before.items():
            if path != ".codex/AGENTS.md":
                self.assertEqual(after.get(path), value)

    def test_rollback_preserves_concurrent_edit_to_already_mutated_path(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        real_action = hosts._perform_action
        calls = 0

        def edit_prior_and_block_future(action, path, value, *args, **kwargs):
            nonlocal calls
            calls += 1
            postimage = real_action(action, path, value, *args, **kwargs)
            if calls == 1:
                instructions.write_bytes(b"concurrent edit")
                skills = codex / "skills"
                skills.mkdir(exist_ok=True)
                (skills / "evergreen").symlink_to(self.home / "replacement")
            return postimage

        with mock.patch.object(hosts, "_perform_action", side_effect=edit_prior_and_block_future):
            result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertIn("manual recovery", " ".join(result.messages))
        self.assertIn("preserved concurrent state", " ".join(result.messages))
        self.assertEqual(instructions.read_bytes(), b"concurrent edit")

    def test_rollback_preserves_postimage_metadata_and_inode_changes(self):
        from evergreen import hosts

        for case in ("chmod", "hardlink", "inode"):
            with self.subTest(case=case):
                home = self.home / case
                codex = home / ".codex"
                codex.mkdir(parents=True)
                instructions = codex / "AGENTS.md"
                instructions.write_bytes(b"original")
                real_action = hosts._perform_action
                calls = 0
                concurrent_inode = None

                def mutate_then_fail(action, path, value, *args, **kwargs):
                    nonlocal calls, concurrent_inode
                    calls += 1
                    if calls == 2:
                        raise OSError("stop after concurrent mutation")
                    postimage = real_action(action, path, value, *args, **kwargs)
                    if case == "chmod":
                        instructions.chmod(0o600)
                    elif case == "hardlink":
                        os.link(instructions, codex / "concurrent-link")
                    else:
                        content = instructions.read_bytes()
                        instructions.unlink()
                        instructions.write_bytes(content)
                        concurrent_inode = instructions.stat().st_ino
                    return postimage

                with mock.patch.object(
                    hosts, "_perform_action", side_effect=mutate_then_fail
                ):
                    result = hosts.install(home, ROOT, "codex")

                self.assertFalse(result.ok)
                self.assertIn("manual recovery", " ".join(result.messages).lower())
                self.assertIn(hosts.BEGIN_MARKER.encode(), instructions.read_bytes())
                if case == "chmod":
                    self.assertEqual(instructions.stat().st_mode & 0o777, 0o600)
                elif case == "hardlink":
                    linked = codex / "concurrent-link"
                    self.assertEqual(instructions.stat().st_ino, linked.stat().st_ino)
                    self.assertEqual(instructions.stat().st_nlink, 2)
                else:
                    self.assertEqual(instructions.stat().st_ino, concurrent_inode)

    def test_existing_instruction_backup_rollback_restores_inode_and_metadata(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        instructions.chmod(0o640)
        before = instructions.stat()
        attribute = self.set_test_xattr(instructions, "write-test", b"preserve")
        timestamp = 1_700_000_000_123_456_789
        os.utime(instructions, ns=(timestamp, timestamp))
        before = instructions.stat()
        real_action = hosts._perform_action
        forward = None

        def fail_after_instruction(action, path, value, *args, **kwargs):
            nonlocal forward
            postimage = real_action(action, path, value, *args, **kwargs)
            if path.name == instructions.name:
                forward = instructions.stat()
                if attribute:
                    self.assertEqual(
                        self.get_test_xattr(instructions, attribute), b"preserve"
                    )
                raise OSError("force rollback after instruction write")
            return postimage

        with mock.patch.object(
            hosts, "_perform_action", side_effect=fail_after_instruction
        ):
            result = hosts.install(self.home, ROOT, "codex")

        after = instructions.stat()
        self.assertFalse(result.ok)
        self.assertEqual(instructions.read_bytes(), b"original")
        self.assertIsNotNone(forward, result.messages)
        self.assertNotEqual(forward.st_ino, before.st_ino)
        self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
        for metadata in (forward, after):
            self.assertEqual((metadata.st_uid, metadata.st_gid), (before.st_uid, before.st_gid))
            self.assertEqual(metadata.st_mode, before.st_mode)
            self.assertEqual(metadata.st_mtime_ns, before.st_mtime_ns)
        if attribute:
            self.assertEqual(self.get_test_xattr(instructions, attribute), b"preserve")
        self.assertFalse(any("evergreen-backup" in path.name for path in codex.iterdir()))

    def test_persistent_temp_write_failure_leaves_original_untouched(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        before = instructions.stat()
        with mock.patch.object(hosts.os, "write", side_effect=OSError("persistent write failure")):
            result = hosts.install(self.home, ROOT, "codex")

        after = instructions.stat()
        self.assertFalse(result.ok)
        self.assertEqual(instructions.read_bytes(), b"original")
        self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
        self.assertEqual((after.st_uid, after.st_gid, after.st_mode), (
            before.st_uid, before.st_gid, before.st_mode,
        ))
        self.assertEqual(
            [path.name for path in codex.iterdir() if path.name != ".evergreen-host.lock"],
            ["AGENTS.md"],
        )

    def test_uninstall_delete_rollback_restores_inode_xattr_and_timestamp(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        self.assertTrue(hosts.install(self.home, ROOT, "codex").ok)
        instructions = codex / "AGENTS.md"
        instructions.chmod(0o640)
        attribute = self.set_test_xattr(
            instructions, "delete-test", b"preserve-delete"
        )
        timestamp = 1_700_000_100_987_654_321
        os.utime(instructions, ns=(timestamp, timestamp))
        before = instructions.stat()
        real_action = hosts._perform_action

        def fail_after_instruction_delete(action, path, value, *args, **kwargs):
            postimage = real_action(action, path, value, *args, **kwargs)
            if path.name == instructions.name:
                self.assertFalse(instructions.exists())
                raise OSError("force rollback after instruction delete")
            return postimage

        with mock.patch.object(
            hosts, "_perform_action", side_effect=fail_after_instruction_delete
        ):
            result = hosts.uninstall(self.home, "codex")

        after = instructions.stat()
        self.assertFalse(result.ok)
        self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
        self.assertEqual((after.st_uid, after.st_gid, after.st_mode), (
            before.st_uid, before.st_gid, before.st_mode,
        ))
        self.assertEqual(after.st_mtime_ns, before.st_mtime_ns)
        if attribute:
            self.assertEqual(
                self.get_test_xattr(instructions, attribute), b"preserve-delete"
            )
        self.assertFalse(any("evergreen-backup" in path.name for path in codex.iterdir()))

    def test_successful_existing_write_preserves_metadata_and_cleans_backup(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        instructions.chmod(0o640)
        attribute = self.set_test_xattr(instructions, "success-test", b"preserve")
        timestamp = 1_700_000_200_111_222_333
        os.utime(instructions, ns=(timestamp, timestamp))
        before = instructions.stat()

        result = hosts.install(self.home, ROOT, "codex")

        after = instructions.stat()
        self.assertTrue(result.ok, result.messages)
        self.assertNotEqual(after.st_ino, before.st_ino)
        self.assertEqual((after.st_uid, after.st_gid, after.st_mode), (
            before.st_uid, before.st_gid, before.st_mode,
        ))
        self.assertEqual((after.st_atime_ns, after.st_mtime_ns), (
            before.st_atime_ns, before.st_mtime_ns,
        ))
        if attribute:
            self.assertEqual(self.get_test_xattr(instructions, attribute), b"preserve")
        self.assertFalse(any("evergreen-backup" in path.name for path in codex.iterdir()))

    def test_publication_failure_leaves_original_and_cleans_all_artifacts(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        before = instructions.stat()
        real_replace = hosts.os.replace

        def fail_publication(source, destination, *args, **kwargs):
            if "evergreen-" in os.fspath(source):
                raise OSError("injected publication failure")
            return real_replace(source, destination, *args, **kwargs)

        with mock.patch.object(hosts.os, "replace", side_effect=fail_publication):
            result = hosts.install(self.home, ROOT, "codex")

        after = instructions.stat()
        self.assertFalse(result.ok)
        self.assertEqual(instructions.read_bytes(), b"original")
        self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
        self.assertEqual(
            [path.name for path in codex.iterdir() if path.name != ".evergreen-host.lock"],
            ["AGENTS.md"],
        )

    def test_rollback_failure_retains_and_reports_original_backup(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        real_action = hosts._perform_action
        real_replace = hosts.os.replace

        def fail_after_instruction(action, path, value, *args, **kwargs):
            postimage = real_action(action, path, value, *args, **kwargs)
            if path.name == instructions.name:
                raise OSError("force rollback")
            return postimage

        def fail_backup_restore(source, destination, *args, **kwargs):
            if "evergreen-backup" in os.fspath(source):
                raise OSError("injected restore failure")
            return real_replace(source, destination, *args, **kwargs)

        with mock.patch.object(
            hosts, "_perform_action", side_effect=fail_after_instruction
        ), mock.patch.object(hosts.os, "replace", side_effect=fail_backup_restore):
            result = hosts.install(self.home, ROOT, "codex")

        backups = [path for path in codex.iterdir() if "evergreen-backup" in path.name]
        self.assertFalse(result.ok)
        self.assertEqual(len(backups), 1)
        self.assertIn(str(backups[0]), " ".join(result.messages))
        self.assertIn("manual recovery", " ".join(result.messages).lower())

    def test_commit_cleanup_failure_retains_and_reports_backup(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        real_unlink = hosts.os.unlink

        def fail_backup_cleanup(path, *args, **kwargs):
            if "evergreen-backup" in os.fspath(path):
                raise OSError("injected cleanup failure")
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(hosts.os, "unlink", side_effect=fail_backup_cleanup):
            result = hosts.install(self.home, ROOT, "codex")

        backups = [path for path in codex.iterdir() if "evergreen-backup" in path.name]
        self.assertFalse(result.ok)
        self.assertEqual(len(backups), 1)
        self.assertIn(str(backups[0]), " ".join(result.messages))
        self.assertIn("manual recovery", " ".join(result.messages).lower())

    def test_source_metadata_must_be_durable_before_backup_or_publication(self):
        from evergreen import hosts

        for operation in ("write", "delete"):
            with self.subTest(operation=operation):
                home = self.home / operation
                codex = home / ".codex"
                codex.mkdir(parents=True)
                instructions = codex / "AGENTS.md"
                if operation == "delete":
                    self.assertTrue(hosts.install(home, ROOT, "codex").ok)
                else:
                    instructions.write_bytes(b"original")
                before = instructions.stat()
                original_fsync = hosts.os.fsync

                def fail_source_fsync(descriptor):
                    if hosts.os.fstat(descriptor).st_ino == before.st_ino:
                        raise OSError("injected source metadata fsync failure")
                    return original_fsync(descriptor)

                with mock.patch.object(hosts.os, "fsync", side_effect=fail_source_fsync):
                    result = (
                        hosts.uninstall(home, "codex") if operation == "delete"
                        else hosts.install(home, ROOT, "codex")
                    )

                self.assertFalse(result.ok)
                self.assertTrue(instructions.exists())
                if instructions.exists():
                    after = instructions.stat()
                    self.assertEqual((after.st_dev, after.st_ino), (
                        before.st_dev, before.st_ino,
                    ))
                self.assertFalse(any(
                    "evergreen-backup" in path.name for path in codex.iterdir()
                ))

    def test_prepublication_crash_artifacts_are_safely_recovered_on_next_install(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        script = f"""
import os
from pathlib import Path
from evergreen import hosts
real_write_journal = hosts._write_journal_at
def crash_after_backup_link(parent_fd, name, journal, *, create):
    if name.startswith('.AGENTS.md.evergreen-journal-') and not create:
        os._exit(91)
    return real_write_journal(parent_fd, name, journal, create=create)
hosts._write_journal_at = crash_after_backup_link
hosts.install(Path({str(self.home)!r}), Path({str(ROOT)!r}), 'codex')
"""

        crashed = subprocess.run(
            [sys.executable, "-c", script], cwd=ROOT,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )

        self.assertEqual(crashed.returncode, 91, crashed.stderr)
        artifacts = sorted(
            path.name for path in codex.iterdir()
            if path.name.startswith(".AGENTS.md.evergreen-")
        )
        self.assertEqual(len(artifacts), 3)
        temporary = next(
            name for name in artifacts
            if "evergreen-backup" not in name and "evergreen-journal" not in name
        )
        backup = next(name for name in artifacts if "evergreen-backup" in name)
        journal = next(name for name in artifacts if "evergreen-journal" in name)
        transaction_ids = {
            temporary.removeprefix(".AGENTS.md.evergreen-"),
            backup.removeprefix(".AGENTS.md.evergreen-backup-"),
            journal.removeprefix(".AGENTS.md.evergreen-journal-"),
        }
        self.assertEqual(len(transaction_ids), 1)
        self.assertEqual(instructions.stat().st_nlink, 2)

        recovered = hosts.install(self.home, ROOT, "codex")

        self.assertTrue(recovered.ok, recovered.messages)
        self.assertEqual(instructions.stat().st_nlink, 1)
        self.assertIn(hosts.BEGIN_MARKER.encode(), instructions.read_bytes())
        self.assertFalse(any(
            path.name.startswith(".AGENTS.md.evergreen-") for path in codex.iterdir()
        ))

    def test_unmatched_transaction_artifact_refuses_with_manual_path(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        artifact = codex / (".AGENTS.md.evergreen-" + "a" * 32)
        artifact.write_bytes(b"unmatched")

        result = hosts.install(self.home, ROOT, "codex")

        rendered = " ".join(result.messages)
        self.assertFalse(result.ok)
        self.assertIn(str(artifact), rendered)
        self.assertIn("manual", rendered.lower())
        self.assertTrue(artifact.exists())

    def test_corrupt_backup_is_retained_and_never_restored(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        real_action = hosts._perform_action

        def corrupt_backup_then_fail(action, path, value, *args, **kwargs):
            postimage = real_action(action, path, value, *args, **kwargs)
            if path.name == instructions.name:
                backup = next(
                    item for item in codex.iterdir() if "evergreen-backup" in item.name
                )
                backup.write_bytes(b"corrupt backup")
                raise OSError("force rollback with corrupt backup")
            return postimage

        with mock.patch.object(
            hosts, "_perform_action", side_effect=corrupt_backup_then_fail
        ):
            result = hosts.install(self.home, ROOT, "codex")

        backups = [item for item in codex.iterdir() if "evergreen-backup" in item.name]
        rendered = " ".join(result.messages)
        self.assertFalse(result.ok)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), b"corrupt backup")
        self.assertIn(hosts.BEGIN_MARKER.encode(), instructions.read_bytes())
        self.assertIn(str(backups[0]), rendered)
        self.assertIn("manual recovery", rendered.lower())
        self.assertNotIn("ordinary recovery completed", rendered)

    def test_replace_success_then_exception_keeps_backup_registered_for_rollback(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        original_inode = instructions.stat().st_ino
        real_replace = hosts.os.replace

        def replace_then_raise(source, destination, *args, **kwargs):
            result = real_replace(source, destination, *args, **kwargs)
            if destination == instructions.name and "evergreen-" in os.fspath(source):
                raise OSError("ambiguous publication result")
            return result

        with mock.patch.object(hosts.os, "replace", side_effect=replace_then_raise):
            result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertEqual(instructions.read_bytes(), b"original")
        self.assertEqual(instructions.stat().st_ino, original_inode)
        self.assertFalse(any(
            item.name.startswith(".AGENTS.md.evergreen-") for item in codex.iterdir()
        ))

    def test_ambiguous_replace_with_failed_inspection_never_deletes_backup(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()
        instructions = codex / "AGENTS.md"
        instructions.write_bytes(b"original")
        real_replace = hosts.os.replace
        real_snapshot_at = hosts._snapshot_at
        publication_attempted = False

        def replace_then_raise(source, destination, *args, **kwargs):
            nonlocal publication_attempted
            result = real_replace(source, destination, *args, **kwargs)
            if destination == instructions.name and "evergreen-" in os.fspath(source):
                publication_attempted = True
                raise OSError("ambiguous publication result")
            return result

        def fail_destination_inspection(path, parent_fd):
            if publication_attempted and path.name == instructions.name:
                raise OSError("destination inspection unavailable")
            return real_snapshot_at(path, parent_fd)

        with (
            mock.patch.object(hosts.os, "replace", side_effect=replace_then_raise),
            mock.patch.object(hosts, "_snapshot_at", side_effect=fail_destination_inspection),
        ):
            result = hosts.install(self.home, ROOT, "codex")

        backups = [item for item in codex.iterdir() if "evergreen-backup" in item.name]
        journals = [item for item in codex.iterdir() if "evergreen-journal" in item.name]
        self.assertFalse(result.ok)
        self.assertEqual(len(backups), 1)
        self.assertEqual(len(journals), 1)
        self.assertIn(str(backups[0]), " ".join(result.messages))

    def test_host_mutation_preflight_requires_python_311(self):
        from evergreen import hosts

        codex = self.home / ".codex"
        codex.mkdir()

        with mock.patch.object(hosts.sys, "version_info", (3, 10, 99)):
            result = hosts.install(self.home, ROOT, "codex")

        self.assertFalse(result.ok)
        self.assertIn("Python 3.11", " ".join(result.messages))
        self.assertEqual(list(codex.iterdir()), [])

    def test_all_host_lock_contention_performs_zero_cross_host_mutation(self):
        import fcntl
        from evergreen import hosts

        claude = self.home / ".claude"
        codex = self.home / ".codex"
        claude.mkdir()
        codex.mkdir()
        stale = claude / (".CLAUDE.md.evergreen-" + "a" * 32)
        stale.write_bytes(b"must remain untouched")
        lock_path = codex / ".evergreen-host.lock"
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = self.snapshot()
        try:
            result = hosts.install(self.home, ROOT, "all")
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

        self.assertFalse(result.ok)
        self.assertIn("another host operation", " ".join(result.messages))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(stale.read_bytes(), b"must remain untouched")

    def test_path_snapshot_records_link_count(self):
        from evergreen import hosts

        path = self.home / "snapshot"
        path.write_bytes(b"value")

        snapshot = hosts._snapshot(path)

        self.assertEqual(snapshot.nlink, 1)

    def test_open_directory_closes_descriptor_when_fstat_fails(self):
        from evergreen import hosts

        directory = self.home / "directory"
        directory.mkdir()
        snapshot = hosts._snapshot(directory, allow_directory=True)
        real_close = hosts.os.close

        with mock.patch.object(hosts.os, "fstat", side_effect=OSError("injected")), \
             mock.patch.object(hosts.os, "close", wraps=real_close) as close:
            with self.assertRaisesRegex(OSError, "injected"):
                hosts._open_directory(snapshot)

        close.assert_called_once()

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

    def test_failure_at_every_action_boundary_runs_ordinary_recovery(self):
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

                def fail_at_boundary(action, path, value, *args, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == boundary and not after:
                        raise OSError(f"injected before action {calls}")
                    postimage = real_action(action, path, value, *args, **kwargs)
                    if calls == boundary and after:
                        raise OSError(f"injected after action {calls}")
                    return postimage

                with mock.patch.object(hosts, "_perform_action", side_effect=fail_at_boundary):
                    result = hosts.install(home, ROOT, "all")

                self.assertFalse(result.ok)
                self.assertIn("ordinary recovery completed", " ".join(result.messages))
                self.assertEqual(self.snapshot(include_directories=True), before)

    def test_rollback_failure_reports_manual_recovery_and_never_success(self):
        from evergreen import hosts

        home = self.home / "rollback failure"
        (home / ".claude").mkdir(parents=True)
        real_action = hosts._perform_action

        def mutate_then_fail(action, path, value, *args, **kwargs):
            real_action(action, path, value, *args, **kwargs)
            raise OSError("injected action failure")

        with mock.patch.object(hosts, "_perform_action", side_effect=mutate_then_fail), \
             mock.patch.object(hosts, "_restore_entry", side_effect=OSError("rollback failed")):
            result = hosts.install(home, ROOT, "claude")

        self.assertFalse(result.ok)
        rendered = " ".join(result.messages).lower()
        self.assertIn("manual recovery", rendered)
        self.assertIn("rollback incomplete", rendered)
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

                def fail_after_action(action, path, value, *args, **kwargs):
                    nonlocal calls
                    calls += 1
                    postimage = real_action(action, path, value, *args, **kwargs)
                    if calls == boundary:
                        raise OSError(f"injected after uninstall action {calls}")
                    return postimage

                with mock.patch.object(hosts, "_perform_action", side_effect=fail_after_action):
                    result = hosts.uninstall(home, "all")

                self.assertFalse(result.ok)
                self.assertIn("ordinary recovery completed", " ".join(result.messages))
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
