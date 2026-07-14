import os
from pathlib import Path
import shutil
from unittest import mock

from evergreen import host_lock, host_snapshot, host_transaction
from tests.host_test_support import HostTestCase


ROOT = Path(__file__).resolve().parents[1]

class HostTests(HostTestCase):

    def test_postcommit_retarget_does_not_attempt_impossible_rollback(self):
        from evergreen.hosts import install

        root = self.home / ".claude"
        root.mkdir()
        self.assertTrue(install(self.home, ROOT, "claude").ok)
        managed = self.home / "managed-claude"
        root.rename(managed)
        root.symlink_to(managed, target_is_directory=True)
        plugin = Path(self.temporary.name) / "postcommit plugin"
        shutil.copytree(ROOT, plugin, symlinks=True)
        displaced = self.home / "postcommit-displaced"
        original = host_transaction._cleanup_committed_entry
        retargeted = False

        def retarget_after_commit(*args, **kwargs):
            nonlocal retargeted
            if not retargeted:
                retargeted = True
                managed.rename(displaced)
                managed.mkdir()
                (managed / "CLAUDE.md").write_text("external replacement\n")
            return original(*args, **kwargs)

        with mock.patch.object(
            host_transaction, "_cleanup_committed_entry",
            side_effect=retarget_after_commit,
        ):
            result = install(self.home, plugin, "claude")

        self.assertTrue(result.ok, result.messages)
        self.assertEqual((managed / "CLAUDE.md").read_text(), "external replacement\n")
        self.assertIn(
            str(plugin).encode(),
            (displaced / ".evergreen-owned.json").read_bytes(),
        )
        self.assertEqual(
            os.readlink(displaced / "skills" / "evergreen"),
            str((plugin / "skills" / "evergreen").resolve()),
        )
        self.assertNotIn("rollback", " ".join(result.messages).lower())

    def test_later_commit_validation_retarget_rolls_back_every_published_path(self):
        from evergreen.hosts import install

        root = self.home / ".claude"
        root.mkdir()
        self.assertTrue(install(self.home, ROOT, "claude").ok)
        managed = self.home / "managed-claude"
        root.rename(managed)
        root.symlink_to(managed, target_is_directory=True)
        plugin = Path(self.temporary.name) / "later commit plugin"
        shutil.copytree(ROOT, plugin, symlinks=True)
        before = {
            "instructions": (managed / "CLAUDE.md").read_bytes(),
            "skill": os.readlink(managed / "skills" / "evergreen"),
            "ownership": (managed / ".evergreen-owned.json").read_bytes(),
        }
        displaced = self.home / "later-commit-displaced"
        original = host_transaction._commit_entry
        calls = 0

        def retarget_before_second_validation(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                managed.rename(displaced)
                managed.mkdir()
                (managed / "CLAUDE.md").write_text("later replacement\n")
            return original(*args, **kwargs)

        with mock.patch.object(
            host_transaction, "_commit_entry",
            side_effect=retarget_before_second_validation,
        ):
            result = install(self.home, plugin, "claude")

        self.assertFalse(result.ok)
        self.assertEqual((managed / "CLAUDE.md").read_text(), "later replacement\n")
        self.assertEqual((displaced / "CLAUDE.md").read_bytes(), before["instructions"])
        self.assertEqual(os.readlink(displaced / "skills" / "evergreen"), before["skill"])
        self.assertEqual((displaced / ".evergreen-owned.json").read_bytes(), before["ownership"])
        self.assertNotIn("rollback incomplete", " ".join(result.messages).lower())

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
        real_verify = host_transaction._verify_preflight

        def change_then_verify(captured):
            changed.write_bytes(b"concurrent replacement")
            return real_verify(captured)

        with mock.patch.object(host_transaction, "_verify_preflight", side_effect=change_then_verify):
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
        real_verify = host_transaction._verify_preflight

        def replace_then_verify(captured):
            instructions.unlink()
            instructions.write_bytes(b"same bytes")
            self.assertNotEqual(instructions.lstat().st_ino, original_inode)
            return real_verify(captured)

        with mock.patch.object(host_transaction, "_verify_preflight", side_effect=replace_then_verify):
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
        real_verify = host_transaction._verify_preflight

        def replace_then_verify(captured):
            skills.rename(replaced)
            skills.mkdir(mode=0o755)
            return real_verify(captured)

        with mock.patch.object(host_transaction, "_verify_preflight", side_effect=replace_then_verify):
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
        real_action = host_transaction._perform_action

        def edit_then_act(action, path, value, *args, **kwargs):
            if path.name == instructions.name:
                instructions.write_bytes(b"concurrent edit")
            return real_action(action, path, value, *args, **kwargs)

        with mock.patch.object(host_transaction, "_perform_action", side_effect=edit_then_act):
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
            real_mkdir("skills", mode, dir_fd=dir_fd)
            raise FileExistsError("concurrent directory won")

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
        real_verify = host_transaction._verify_preflight

        def replace_then_verify(captured):
            link.unlink()
            link.symlink_to(replacement, target_is_directory=True)
            return real_verify(captured)

        before_claude = self.snapshot(include_directories=True)
        with mock.patch.object(host_transaction, "_verify_preflight", side_effect=replace_then_verify):
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
        real_action = host_transaction._perform_action
        calls = 0

        def replace_future_target(action, path, value, *args, **kwargs):
            nonlocal calls
            calls += 1
            postimage = real_action(action, path, value, *args, **kwargs)
            if calls == 1:
                changed.write_bytes(b"concurrent replacement")
            return postimage

        with mock.patch.object(host_transaction, "_perform_action", side_effect=replace_future_target):
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
        real_action = host_transaction._perform_action
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

        with mock.patch.object(host_transaction, "_perform_action", side_effect=edit_prior_and_block_future):
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
                real_action = host_transaction._perform_action
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
                        replacement = instructions.with_name("concurrent-replacement")
                        replacement.write_bytes(content)
                        os.replace(replacement, instructions)
                        concurrent_inode = instructions.stat().st_ino
                    return postimage

                with mock.patch.object(
                    host_transaction, "_perform_action", side_effect=mutate_then_fail
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
        real_action = host_transaction._perform_action
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
            host_transaction, "_perform_action", side_effect=fail_after_instruction
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
        real_action = host_transaction._perform_action

        def fail_after_instruction_delete(action, path, value, *args, **kwargs):
            postimage = real_action(action, path, value, *args, **kwargs)
            if path.name == instructions.name:
                self.assertFalse(instructions.exists())
                raise OSError("force rollback after instruction delete")
            return postimage

        with mock.patch.object(
            host_transaction, "_perform_action", side_effect=fail_after_instruction_delete
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
        real_action = host_transaction._perform_action
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
            host_transaction, "_perform_action", side_effect=fail_after_instruction
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

    def test_transaction_engine_deletes_and_commits_empty_directory(self):
        directory = self.home / "empty"
        directory.mkdir()
        before = host_snapshot.snapshot(directory, allow_directory=True)
        parent = host_snapshot.snapshot(self.home, allow_directory=True)
        rollback = []
        conflicts = []
        parent_fd = host_snapshot.open_directory(parent)
        try:
            after = host_transaction._perform_action(
                "delete", directory, None, parent_fd=parent_fd, expected=before,
                parent_snapshot=parent, rollback_entries=rollback,
                conflicts=conflicts,
            )
        finally:
            os.close(parent_fd)

        self.assertEqual(after.kind, "absent")
        self.assertEqual(len(rollback), 1)
        host_transaction._commit_entry(rollback[0])
        host_transaction._cleanup_committed_entry(rollback[0])
        self.assertFalse(directory.exists())
        self.assertFalse(any("evergreen-" in item.name for item in self.home.iterdir()))

    def test_host_modules_stay_within_maintainability_budgets(self):
        hosts_lines = (ROOT / "evergreen" / "hosts.py").read_text().splitlines()
        self.assertLess(len(hosts_lines), 700)
        for name in (
            "host_lock.py", "host_snapshot.py", "host_journal.py",
            "host_commit.py", "host_transaction.py",
        ):
            module = ROOT / "evergreen" / name
            self.assertTrue(module.exists())
            self.assertLess(len(module.read_text().splitlines()), 700, name)
        for module in (ROOT / "tests").glob("test_host*.py"):
            self.assertLess(len(module.read_text().splitlines()), 1000, module.name)

    def test_host_module_dependencies_follow_ownership_boundaries(self):
        import ast

        dependencies = {}
        for name in (
            "host_lock", "host_snapshot", "host_journal", "host_commit",
            "host_transaction",
        ):
            tree = ast.parse((ROOT / "evergreen" / f"{name}.py").read_text())
            dependencies[name] = {
                node.module.rsplit(".", 1)[-1]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
        self.assertNotIn("host_transaction", dependencies["host_lock"])
        self.assertNotIn("host_transaction", dependencies["host_snapshot"])
        self.assertNotIn("host_transaction", dependencies["host_journal"])
        self.assertNotIn("host_transaction", dependencies["host_commit"])
        self.assertIn("host_journal", dependencies["host_transaction"])

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
        real_snapshot_at = host_transaction._snapshot_at
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
            mock.patch.object(host_transaction, "_snapshot_at", side_effect=fail_destination_inspection),
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

        with mock.patch.object(host_lock.sys, "version_info", (3, 10, 99)):
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
        lock_fd = os.open(self.home, os.O_RDONLY)
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
                host_snapshot.open_directory(snapshot)

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
                real_action = host_transaction._perform_action
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

                with mock.patch.object(host_transaction, "_perform_action", side_effect=fail_at_boundary):
                    result = hosts.install(home, ROOT, "all")

                self.assertFalse(result.ok)
                self.assertIn("ordinary recovery completed", " ".join(result.messages))
                self.assertEqual(self.snapshot(include_directories=True), before)

    def test_rollback_failure_reports_manual_recovery_and_never_success(self):
        from evergreen import hosts

        home = self.home / "rollback failure"
        (home / ".claude").mkdir(parents=True)
        real_action = host_transaction._perform_action

        def mutate_then_fail(action, path, value, *args, **kwargs):
            real_action(action, path, value, *args, **kwargs)
            raise OSError("injected action failure")

        with mock.patch.object(host_transaction, "_perform_action", side_effect=mutate_then_fail), \
             mock.patch.object(host_transaction, "_restore_entry", side_effect=OSError("rollback failed")):
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
                real_action = host_transaction._perform_action
                calls = 0

                def fail_after_action(action, path, value, *args, **kwargs):
                    nonlocal calls
                    calls += 1
                    postimage = real_action(action, path, value, *args, **kwargs)
                    if calls == boundary:
                        raise OSError(f"injected after uninstall action {calls}")
                    return postimage

                with mock.patch.object(host_transaction, "_perform_action", side_effect=fail_after_action):
                    result = hosts.uninstall(home, "all")

                self.assertFalse(result.ok)
                self.assertIn("ordinary recovery completed", " ".join(result.messages))
                self.assertEqual(self.snapshot(include_directories=True), before)
