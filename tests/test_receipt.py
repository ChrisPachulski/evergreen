import copy
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from evergreen.receipt import ReceiptError, build_receipt


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.repo = base / "repo"
        self.origin = base / "origin.git"
        self.repo.mkdir()
        self.run_git(self.repo, "init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        (self.repo / "tracked").write_text("original\n")
        self.git("add", "tracked")
        self.git("commit", "-qm", "initial")
        self.run_git(self.repo.parent, "init", "-q", "--bare", str(self.origin))
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "-qu", "origin", "main")
        self.run_git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def run_git(directory, *args):
        return subprocess.run(
            ["git", "-C", str(directory), *args],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def git(self, *args):
        return self.run_git(self.repo, *args)

    def test_clean_synchronized_receipt_is_deterministic(self):
        first = build_receipt(self.repo)
        second = build_receipt(self.repo)

        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["repository"]["root"], str(self.repo.resolve()))
        self.assertEqual(first["repository"]["name"], "origin")
        self.assertEqual(first["repository"]["branch"], "main")
        self.assertEqual(first["repository"]["head"], self.git("rev-parse", "HEAD"))
        self.assertEqual(first["repository"]["upstream"], "origin/main")
        self.assertEqual(
            (first["repository"]["ahead"], first["repository"]["behind"]),
            (0, 0),
        )
        self.assertEqual(
            {
                key: first["repository"][key]
                for key in ("staged", "unstaged", "untracked")
            },
            {"staged": 0, "unstaged": 0, "untracked": 0},
        )
        self.assertTrue(first["repository"]["clean"])
        self.assertEqual(
            first["release"],
            {"external_state": "unverified", "local_tags": []},
        )
        self.assertIsNone(first["benchmark"])

    def test_counts_staged_unstaged_and_untracked_without_counting_ignored(self):
        (self.repo / ".gitignore").write_text("ignored\n")
        (self.repo / "staged").write_text("staged\n")
        self.git("add", ".gitignore", "staged")
        (self.repo / "tracked").write_text("changed\n")
        (self.repo / "untracked").write_text("new\n")
        (self.repo / "ignored").write_text("ignored\n")

        receipt = build_receipt(self.repo)

        self.assertEqual(
            (
                receipt["repository"]["staged"],
                receipt["repository"]["unstaged"],
                receipt["repository"]["untracked"],
            ),
            (2, 1, 1),
        )
        self.assertFalse(receipt["repository"]["clean"])

    def test_index_visibility_flags_fail_closed_instead_of_hiding_changes(self):
        for enable, disable in (
            ("--assume-unchanged", "--no-assume-unchanged"),
            ("--skip-worktree", "--no-skip-worktree"),
        ):
            with self.subTest(flag=enable):
                self.git("update-index", enable, "tracked")
                (self.repo / "tracked").write_text("hidden change\n")
                with self.assertRaisesRegex(ReceiptError, "visibility"):
                    build_receipt(self.repo)
                self.git("update-index", disable, "tracked")
                (self.repo / "tracked").write_text("original\n")

    def test_submodule_ignore_configuration_cannot_hide_changes(self):
        source = self.repo.parent / "submodule-source"
        source.mkdir()
        self.run_git(source, "init", "-q", "-b", "main")
        self.run_git(source, "config", "user.email", "test@example.com")
        self.run_git(source, "config", "user.name", "Test")
        (source / "tracked").write_text("original\n")
        self.run_git(source, "add", "tracked")
        self.run_git(source, "commit", "-qm", "initial")
        self.git(
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "add",
            "-q",
            str(source),
            "vendor/submodule",
        )
        self.git("commit", "-qm", "add submodule")
        (self.repo / "vendor" / "submodule" / "tracked").write_text("changed\n")
        self.git("config", "diff.ignoreSubmodules", "all")
        self.git("config", "submodule.vendor/submodule.ignore", "all")

        with self.assertRaisesRegex(ReceiptError, "submodules"):
            build_receipt(self.repo)

    def test_file_mode_configuration_cannot_hide_changes(self):
        tracked = self.repo / "tracked"
        tracked.chmod(tracked.stat().st_mode | stat.S_IXUSR)
        self.git("config", "core.fileMode", "false")

        repository = build_receipt(self.repo)["repository"]

        self.assertEqual(repository["unstaged"], 1)
        self.assertFalse(repository["clean"])

    def test_symlink_configuration_cannot_hide_type_changes(self):
        link = self.repo / "link"
        link.symlink_to("target")
        self.git("add", "link")
        self.git("commit", "-qm", "add symlink")
        link.unlink()
        link.write_text("target")
        self.git("config", "core.symlinks", "false")

        repository = build_receipt(self.repo)["repository"]

        self.assertEqual(repository["unstaged"], 1)
        self.assertFalse(repository["clean"])

    def test_rename_source_path_is_not_counted_as_an_untracked_record(self):
        old = self.repo / "? old"
        old.write_text("rename me\n")
        self.git("add", old.name)
        self.git("commit", "-qm", "add tricky rename source")
        self.git("mv", old.name, "renamed")

        repository = build_receipt(self.repo)["repository"]

        self.assertEqual(repository["staged"], 1)
        self.assertEqual(repository["unstaged"], 0)
        self.assertEqual(repository["untracked"], 0)

    def test_repository_rename_configuration_cannot_change_counts(self):
        self.git("config", "status.renames", "false")
        old = self.repo / "old"
        old.write_text("rename me\n")
        self.git("add", old.name)
        self.git("commit", "-qm", "add rename source")
        self.git("mv", old.name, "new")

        repository = build_receipt(self.repo)["repository"]

        self.assertEqual(repository["staged"], 1)
        self.assertEqual(repository["unstaged"], 0)

    def test_repository_rename_limits_cannot_change_counts(self):
        for index in range(10):
            path = self.repo / f"old-{index}"
            path.write_text(f"shared content {index}\nold suffix\n")
        self.git("add", ".")
        self.git("commit", "-qm", "add rename sources")
        for index in range(10):
            old = self.repo / f"old-{index}"
            new = self.repo / f"new-{index}"
            old.rename(new)
            new.write_text(f"shared content {index}\nnew suffix\n")
        self.git("add", "-A")
        self.git("config", "status.renameLimit", "1")
        self.git("config", "diff.renameLimit", "1")

        repository = build_receipt(self.repo)["repository"]

        self.assertEqual(repository["staged"], 10)
        self.assertEqual(repository["unstaged"], 0)

    def test_detached_head_has_no_branch_or_upstream(self):
        self.git("checkout", "-q", "--detach")

        repository = build_receipt(self.repo)["repository"]

        self.assertIsNone(repository["branch"])
        self.assertTrue(repository["detached"])
        self.assertIsNone(repository["upstream"])
        self.assertIsNone(repository["ahead"])
        self.assertIsNone(repository["behind"])

    def test_legal_branch_named_detached_is_not_reported_as_detached_head(self):
        self.git("checkout", "-qb", "(detached)")

        repository = build_receipt(self.repo)["repository"]

        self.assertEqual(repository["branch"], "(detached)")
        self.assertFalse(repository["detached"])

    def test_missing_origin_and_upstream_are_data(self):
        self.git("branch", "--unset-upstream")
        without_upstream = build_receipt(self.repo)["repository"]
        self.assertIsNone(without_upstream["upstream"])
        self.assertIsNone(without_upstream["ahead"])
        self.assertIsNone(without_upstream["behind"])

        self.git("remote", "remove", "origin")
        self.assertIsNone(build_receipt(self.repo)["repository"]["origin"])

    def test_ahead_and_behind_are_reported(self):
        (self.repo / "local").write_text("local\n")
        self.git("add", "local")
        self.git("commit", "-qm", "local")

        peer = self.repo.parent / "peer"
        self.run_git(self.repo.parent, "clone", "-q", str(self.origin), str(peer))
        self.run_git(peer, "config", "user.email", "peer@example.com")
        self.run_git(peer, "config", "user.name", "Peer")
        (peer / "remote").write_text("remote\n")
        self.run_git(peer, "add", "remote")
        self.run_git(peer, "commit", "-qm", "remote")
        self.run_git(peer, "push", "-q", "origin", "main")
        self.git("fetch", "-q", "origin")

        repository = build_receipt(self.repo)["repository"]

        self.assertEqual((repository["ahead"], repository["behind"]), (1, 1))

    def test_only_sorted_tags_pointing_at_head_are_returned(self):
        self.git("tag", "old")
        (self.repo / "next").write_text("next\n")
        self.git("add", "next")
        self.git("commit", "-qm", "next")
        self.git("tag", "z-last")
        self.git("tag", "a-first")

        self.assertEqual(
            build_receipt(self.repo)["release"]["local_tags"],
            ["a-first", "z-last"],
        )

    def test_path_inside_worktree_resolves_repository_root(self):
        nested = self.repo / "nested" / "deeper"
        nested.mkdir(parents=True)

        self.assertEqual(
            build_receipt(nested)["repository"]["root"],
            str(self.repo.resolve()),
        )

    def test_repository_root_and_origin_preserve_surrounding_spaces(self):
        renamed = self.repo.with_name(" repo ")
        self.repo.rename(renamed)
        self.repo = renamed
        origin = "/tmp/canonical.git "
        self.git("remote", "set-url", "origin", origin)

        repository = build_receipt(self.repo)["repository"]

        self.assertEqual(repository["root"], str(self.repo.resolve()))
        self.assertEqual(repository["origin"], origin)

    def test_origin_project_name_wins_for_ordinary_and_linked_directory_names(self):
        remotes = (
            "https://example.invalid/owner/project.git",
            "ssh://example.invalid/owner/project.git",
            "git://example.invalid/owner/project.git",
            "example.invalid:owner/project.git",
        )
        for directory_name in ("repo", "evidence-receipts"):
            if self.repo.name != directory_name:
                renamed = self.repo.with_name(directory_name)
                self.repo.rename(renamed)
                self.repo = renamed
            for remote in remotes:
                with self.subTest(directory=directory_name, remote=remote):
                    self.git("remote", "set-url", "origin", remote)
                    repository = build_receipt(self.repo)["repository"]
                    self.assertEqual(repository["name"], "project")
                    self.assertEqual(repository["root"], str(self.repo.resolve()))

    def test_file_and_filesystem_origins_supply_canonical_project_name(self):
        remotes = (
            "file:///srv/git/canonical.git",
            "/srv/git/canonical.git",
            "../git/canonical.git",
        )
        for remote in remotes:
            with self.subTest(remote=remote):
                self.git("remote", "set-url", "origin", remote)
                self.assertEqual(
                    build_receipt(self.repo)["repository"]["name"],
                    "canonical",
                )

    def test_missing_or_unusable_origin_falls_back_to_worktree_name(self):
        self.git("remote", "remove", "origin")
        self.assertEqual(build_receipt(self.repo)["repository"]["name"], "repo")

        self.git("remote", "add", "origin", "https://example.invalid/")
        self.assertEqual(build_receipt(self.repo)["repository"]["name"], "repo")

        self.git(
            "remote",
            "set-url",
            "origin",
            "helper::https://alice:secret@example.invalid/owner/project.git",
        )
        self.assertEqual(build_receipt(self.repo)["repository"]["name"], "repo")

    def test_non_repository_and_missing_head_are_rejected(self):
        outside = self.repo.parent / "outside"
        outside.mkdir()
        with self.assertRaises(ReceiptError):
            build_receipt(outside)

        empty = self.repo.parent / "empty"
        empty.mkdir()
        self.run_git(empty, "init", "-q", "-b", "main")
        with self.assertRaises(ReceiptError):
            build_receipt(empty)

    def test_remote_credentials_are_redacted(self):
        self.git(
            "remote",
            "set-url",
            "origin",
            "https://alice:secret@example.invalid/owner/repo.git",
        )
        http_origin = build_receipt(self.repo)["repository"]["origin"]
        self.assertEqual(
            http_origin,
            "https://[redacted]@example.invalid/owner/repo.git",
        )
        self.assertNotIn("alice", http_origin)
        self.assertNotIn("secret", http_origin)

        self.git("remote", "set-url", "origin", "deploy@example.invalid:owner/repo.git")
        scp_origin = build_receipt(self.repo)["repository"]["origin"]
        self.assertEqual(scp_origin, "[redacted]@example.invalid:owner/repo.git")
        self.assertNotIn("deploy", scp_origin)

    def test_remote_https_query_is_removed(self):
        self.git(
            "remote",
            "set-url",
            "origin",
            "https://example.invalid/owner/repo.git?access_token=query-secret",
        )

        origin = build_receipt(self.repo)["repository"]["origin"]

        self.assertEqual(origin, "https://example.invalid/owner/repo.git")
        self.assertNotIn("access_token", origin)
        self.assertNotIn("query-secret", origin)

    def test_remote_fragment_is_removed(self):
        self.git(
            "remote",
            "set-url",
            "origin",
            "https://example.invalid/owner/repo.git#fragment-secret",
        )

        origin = build_receipt(self.repo)["repository"]["origin"]

        self.assertEqual(origin, "https://example.invalid/owner/repo.git")
        self.assertNotIn("fragment-secret", origin)

    def test_remote_helper_with_nested_credentials_fails_closed(self):
        self.git(
            "remote",
            "set-url",
            "origin",
            "helper::https://alice:secret@example.invalid/owner/repo.git",
        )

        origin = build_receipt(self.repo)["repository"]["origin"]

        self.assertEqual(origin, "[redacted]")

    def test_remote_helpers_and_unsupported_schemes_fail_closed_without_at_sign(self):
        remotes = (
            "tokenhelper::SUPERSECRET/owner/repo",
            "ext::sh -c curl -H Authorization:Bearer-SECRET https://example.invalid/repo",
            "unknown://example.invalid/secret/repo.git",
        )
        for remote in remotes:
            with self.subTest(remote=remote):
                self.git("remote", "set-url", "origin", remote)
                self.assertEqual(
                    build_receipt(self.repo)["repository"]["origin"],
                    "[redacted]",
                )

    def test_malformed_bracketed_remote_fails_closed(self):
        self.git(
            "remote",
            "set-url",
            "origin",
            "https://alice:secret@[example.invalid/owner/repo.git",
        )

        origin = build_receipt(self.repo)["repository"]["origin"]

        self.assertEqual(origin, "[redacted]")

    def test_benign_normal_remotes_are_preserved(self):
        remotes = (
            "https://example.invalid/owner/repo.git",
            "ssh://example.invalid/owner/repo.git",
            "git://example.invalid/owner/repo.git",
            "file:///tmp/repo.git",
            "/tmp/repo.git",
            "../tmp/repo.git",
            "example.invalid:owner/repo.git",
        )
        for remote in remotes:
            with self.subTest(remote=remote):
                self.git("remote", "set-url", "origin", remote)
                self.assertEqual(
                    build_receipt(self.repo)["repository"]["origin"], remote
                )

    def test_hostile_fsmonitor_and_trace_environment_cannot_execute_or_write(self):
        hook_marker = self.repo / "fsmonitor-executed"
        trace_marker = self.repo / "git-trace"
        hook = self.repo.parent / "hostile-fsmonitor"
        hook.write_text(
            f"#!{sys.executable}\n"
            "from pathlib import Path\n"
            f"Path({str(hook_marker)!r}).write_text('executed')\n"
        )
        hook.chmod(0o755)
        self.git("config", "core.fsmonitor", str(hook))

        with mock.patch.dict(
            os.environ,
            {"GIT_TRACE2_EVENT": str(trace_marker)},
            clear=False,
        ):
            build_receipt(self.repo)

        self.assertFalse(hook_marker.exists())
        self.assertFalse(trace_marker.exists())

    def test_every_git_call_disables_lazy_fetching(self):
        from evergreen import receipt as module

        environments = []
        original = module.subprocess.Popen

        def recording_popen(*args, **kwargs):
            environments.append(dict(kwargs["env"]))
            return original(*args, **kwargs)

        with mock.patch.object(module.subprocess, "Popen", side_effect=recording_popen):
            build_receipt(self.repo)

        self.assertTrue(environments)
        self.assertTrue(all(
            environment.get("GIT_NO_LAZY_FETCH") == "1"
            for environment in environments
        ))

    def test_repository_clean_filter_cannot_execute(self):
        marker = self.repo / "filter-executed"
        hook = self.repo.parent / "hostile-clean-filter"
        hook.write_text(
            f"#!{sys.executable}\n"
            "import sys\n"
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
            "sys.stdout.buffer.write(sys.stdin.buffer.read())\n"
        )
        hook.chmod(0o755)
        (self.repo / ".gitattributes").write_text("tracked filter=evil\n")
        self.git("add", ".gitattributes")
        self.git("commit", "-qm", "add hostile filter attribute")
        self.git("config", "filter.evil.clean", str(hook))
        (self.repo / "tracked").write_text("changed\n")

        with self.assertRaisesRegex(ReceiptError, "external Git filters"):
            build_receipt(self.repo)
        self.assertFalse(marker.exists())

    def test_repository_long_running_process_filter_cannot_execute(self):
        marker = self.repo / "process-filter-executed"
        descendant = self.repo / "process-filter-descendant"
        hook = self.repo.parent / "hostile-process-filter"
        hook.write_text(
            f"#!{sys.executable}\n"
            "import subprocess, sys, time\n"
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_text('executed')\n"
            "subprocess.Popen([sys.executable, '-c', "
            f"\"import time; from pathlib import Path; time.sleep(1); Path({str(descendant)!r}).write_text('executed')\"] )\n"
            "time.sleep(10)\n"
        )
        hook.chmod(0o755)
        (self.repo / ".gitattributes").write_text("tracked filter=evil\n")
        self.git("add", ".gitattributes")
        self.git("commit", "-qm", "add hostile process-filter attribute")
        self.git("config", "filter.evil.process", str(hook))
        (self.repo / "tracked").write_text("changed\n")

        with self.assertRaisesRegex(ReceiptError, "external Git filters"):
            build_receipt(self.repo)
        self.assertFalse(marker.exists())
        self.assertFalse(descendant.exists())

    def test_stub_git_timeout_and_output_limit_are_bounded_operational_errors(self):
        from evergreen import receipt as module

        stub_source = (
            f"#!{sys.executable}\n"
            "import sys, time\n"
            "if 'timeout' in __file__:\n"
            "    time.sleep(1)\n"
            "else:\n"
            f"    sys.stdout.buffer.write(b'x' * ({module.MAX_GIT_OUTPUT_BYTES} + 1))\n"
            "    sys.stdout.buffer.flush()\n"
            "    time.sleep(1)\n"
        )
        timeout_stub = self.repo.parent / "git-stub-timeout"
        output_stub = self.repo.parent / "git-stub-output"
        for stub in (timeout_stub, output_stub):
            stub.write_text(stub_source)
            stub.chmod(0o755)

        with mock.patch.object(module, "_GIT_EXECUTABLE", str(timeout_stub)), \
                mock.patch.object(module, "GIT_TIMEOUT_SECONDS", 0.01), \
                self.assertRaisesRegex(module.ReceiptOperationalError, "timed out"):
            build_receipt(self.repo)

        started = time.monotonic()
        with mock.patch.object(module, "_GIT_EXECUTABLE", str(output_stub)), \
                mock.patch.object(module, "GIT_TIMEOUT_SECONDS", 2), \
                self.assertRaisesRegex(module.ReceiptOperationalError, "too much output"):
            build_receipt(self.repo)
        self.assertLess(time.monotonic() - started, 0.75)

    def test_unexpected_output_read_failure_is_bounded_and_operational(self):
        from evergreen import receipt as module

        with mock.patch.object(
            module,
            "_bounded_process_output",
            side_effect=OSError("read failed"),
        ), self.assertRaisesRegex(
            module.ReceiptOperationalError,
            "could not be read",
        ):
            build_receipt(self.repo)

    def test_only_declared_git_exit_codes_are_treated_as_missing(self):
        from evergreen import receipt as module

        stub = self.repo.parent / "git-stub-exit"
        stub.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(int(sys.argv[-1]))\n")
        stub.chmod(0o755)
        with mock.patch.object(module, "_GIT_EXECUTABLE", str(stub)):
            self.assertIsNone(module._git(self.repo, "2", missing_codes=(2,)))
            with self.assertRaises(module.ReceiptOperationalError):
                module._git(self.repo, "3", missing_codes=(2,))

    def test_unsupported_platform_fails_before_posix_operations(self):
        from evergreen import receipt as module

        for os_name, platform in (("nt", "win32"), ("posix", "freebsd14")):
            with self.subTest(platform=platform), mock.patch.object(
                module.os,
                "name",
                os_name,
            ), mock.patch.object(
                module.sys,
                "platform",
                platform,
            ), self.assertRaisesRegex(
                module.ReceiptOperationalError,
                "macOS or Linux",
            ):
                build_receipt(self.repo)

    def test_tag_query_is_bound_to_captured_commit_not_symbolic_head(self):
        from evergreen import receipt as module

        calls = []
        original = module._git

        def recording_git(root, *args, **kwargs):
            calls.append(args)
            return original(root, *args, **kwargs)

        with mock.patch.object(module, "_git", side_effect=recording_git):
            receipt = build_receipt(self.repo)

        head = receipt["repository"]["head"]
        self.assertIn(("tag", "--points-at", head), calls)
        self.assertNotIn(("tag", "--points-at", "HEAD"), calls)

    def test_torn_repository_state_is_retried_then_refused(self):
        from evergreen import receipt as module

        stable = module._status(self.repo)
        moving = {**stable, "staged": stable["staged"] + 1, "clean": False}
        with mock.patch.object(
            module,
            "_status",
            side_effect=(stable, moving, stable, moving),
        ), self.assertRaisesRegex(
            module.ReceiptOperationalError,
            "changed while receipt was collected",
        ):
            build_receipt(self.repo)

    def test_torn_benchmark_identity_is_retried_then_refused(self):
        from evergreen import receipt as module

        manifest = Path("bench/manifest.json")
        self.write_benchmark_manifest(self.benchmark_manifest())
        first = {"evaluated_release": "0.4.0"}
        second = {"evaluated_release": "0.5.0"}
        with mock.patch.object(
            module,
            "_benchmark_identity",
            side_effect=(first, second, first, second),
        ), self.assertRaisesRegex(
            module.ReceiptOperationalError,
            "changed while receipt was collected",
        ):
            build_receipt(self.repo, manifest)

    def test_receipt_does_not_change_repository_bytes(self):
        (self.repo / "untracked").write_text("leave me\n")

        before = self.repository_snapshot()
        build_receipt(self.repo)
        after = self.repository_snapshot()

        self.assertEqual(after, before)

    def test_nonmutation_snapshot_covers_modes_and_linked_common_git_dir(self):
        before_mode = self.repository_snapshot()
        tracked = self.repo / "tracked"
        tracked.chmod(tracked.stat().st_mode | stat.S_IXUSR)
        self.assertNotEqual(self.repository_snapshot(), before_mode)
        tracked.chmod(tracked.stat().st_mode & ~stat.S_IXUSR)

        linked = self.repo.parent / "linked-worktree"
        self.git("worktree", "add", "-q", "-b", "receipt-linked", str(linked))
        before = self.repository_snapshot(linked)
        build_receipt(linked)
        after = self.repository_snapshot(linked)

        self.assertNotEqual(before["git_dir_path"], before["git_common_dir_path"])
        self.assertEqual(after, before)

    def test_valid_publication_manifest_returns_declaration_only_identity(self):
        self.write_benchmark_manifest(self.benchmark_manifest())

        benchmark = build_receipt(
            self.repo, Path("bench/manifest.json")
        )["benchmark"]

        self.assertEqual(benchmark, {
            "artifact_count": 5,
            "evaluated_release": "0.4.0",
            "evidence_state": "declared_publication",
            "judge_sha256": "e" * 64,
            "languages": ["Java", "Python", "go", "rust", "typescript"],
            "manifest": "bench/manifest.json",
            "protocol": "unverified",
            "provenance_commit": "a" * 40,
            "provider": "codex",
            "report": "bench/report.md",
            "resolver": "unverified",
        })

    def test_untracked_benchmark_manifest_is_not_a_declared_publication(self):
        manifest = self.benchmark_manifest()
        path = self.repo / "bench" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest))

        with self.assertRaisesRegex(ReceiptError, "tracked"):
            build_receipt(self.repo, Path("bench/manifest.json"))

    def test_dirty_benchmark_manifest_cannot_masquerade_as_publication(self):
        manifest = self.benchmark_manifest()
        self.write_benchmark_manifest(manifest)
        manifest["evaluated_release"] = "0.5.0"
        (self.repo / "bench" / "manifest.json").write_text(json.dumps(manifest))

        with self.assertRaisesRegex(ReceiptError, "captured HEAD"):
            build_receipt(self.repo, Path("bench/manifest.json"))

    def test_missing_head_path_and_unavailable_promised_blob_have_distinct_errors(self):
        from evergreen import receipt as module

        with mock.patch.object(module, "_git", return_value=""), self.assertRaises(
            ReceiptError
        ) as absent:
            module._head_regular_blob(self.repo, "a" * 40, "bench/manifest.json")
        self.assertNotIsInstance(absent.exception, module.ReceiptOperationalError)

        listing = f"100644 blob {'b' * 40}\tbench/manifest.json\0"
        with mock.patch.object(
            module,
            "_git",
            side_effect=(
                listing,
                module.ReceiptOperationalError("promised blob is unavailable"),
            ),
        ), self.assertRaisesRegex(
            module.ReceiptOperationalError,
            "promised blob",
        ):
            module._head_regular_blob(self.repo, "a" * 40, "bench/manifest.json")

    def test_benchmark_judge_resolver_and_protocol_identity_are_validated(self):
        manifest = self.benchmark_manifest()
        manifest["provenance"]["resolver"] = "v2"
        manifest["provenance"]["protocol"] = "java-git-window-v1"
        self.write_benchmark_manifest(manifest)

        benchmark = build_receipt(
            self.repo, Path("bench/manifest.json")
        )["benchmark"]

        self.assertEqual(benchmark["judge_sha256"], "e" * 64)
        self.assertEqual(benchmark["resolver"], "v2")
        self.assertEqual(benchmark["protocol"], "java-git-window-v1")

        for value in (None, "short", "E" * 64, "g" * 64):
            with self.subTest(judge_sha256=value):
                invalid = self.benchmark_manifest()
                if value is None:
                    invalid["provenance"].pop("judge_sha256")
                else:
                    invalid["provenance"]["judge_sha256"] = value
                self.write_benchmark_manifest(invalid)
                with self.assertRaisesRegex(ReceiptError, "judge"):
                    build_receipt(self.repo, Path("bench/manifest.json"))

        for field in ("resolver", "protocol"):
            for value in ("", "  ", 2):
                with self.subTest(field=field, value=value):
                    invalid = self.benchmark_manifest()
                    invalid["provenance"][field] = value
                    self.write_benchmark_manifest(invalid)
                    with self.assertRaisesRegex(ReceiptError, field):
                        build_receipt(self.repo, Path("bench/manifest.json"))

    def test_manifest_bytes_and_json_are_bounded_and_well_formed(self):
        from evergreen import receipt as module

        path = self.repo / "bench" / "manifest.json"
        self.write_benchmark_manifest(self.benchmark_manifest())

        path.write_bytes(b"\xff")
        with self.assertRaisesRegex(ReceiptError, "UTF-8"):
            build_receipt(self.repo, Path("bench/manifest.json"))

        path.write_text("{")
        with self.assertRaisesRegex(ReceiptError, "JSON"):
            build_receipt(self.repo, Path("bench/manifest.json"))

        path.write_text("[]")
        with self.assertRaisesRegex(ReceiptError, "object"):
            build_receipt(self.repo, Path("bench/manifest.json"))

        manifest = self.benchmark_manifest()
        manifest["publication"]["coverage_threshold"] = float("nan")
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ReceiptError, "JSON"):
            build_receipt(self.repo, Path("bench/manifest.json"))

        path.write_text("{}" + " " * 10)
        with mock.patch.object(module, "MAX_MANIFEST_BYTES", 4), \
                self.assertRaisesRegex(ReceiptError, "too large"):
            build_receipt(self.repo, Path("bench/manifest.json"))

    def test_manifest_path_must_be_normalized_safe_regular_file(self):
        manifest = self.benchmark_manifest()
        self.write_benchmark_manifest(manifest)
        outside = self.repo.parent / "outside.json"
        outside.write_text(json.dumps(manifest))
        (self.repo / "bench" / "manifest-link.json").symlink_to("manifest.json")
        (self.repo / "linked-bench").symlink_to("bench", target_is_directory=True)

        invalid = (
            Path("."),
            Path("../outside.json"),
            (self.repo / "bench" / "manifest.json").resolve(),
            Path("bench/manifest-link.json"),
            Path("linked-bench/manifest.json"),
            Path("bench"),
        )
        for supplied in invalid:
            with self.subTest(supplied=supplied), self.assertRaises(ReceiptError):
                build_receipt(self.repo, supplied)

    def test_manifest_read_remains_bound_to_opened_file_during_path_swap(self):
        from evergreen import receipt as module

        inside = self.benchmark_manifest()
        outside = copy.deepcopy(inside)
        outside["evaluated_release"] = "outside"
        self.write_benchmark_manifest(inside)
        manifest = self.repo / "bench" / "manifest.json"
        displaced = self.repo / "bench" / "opened-manifest.json"
        outside_path = self.repo.parent / "outside-manifest.json"
        outside_path.write_text(json.dumps(outside))
        original_open = os.open
        swapped = False

        def swapping_open(path, flags, *args, **kwargs):
            nonlocal swapped
            descriptor = original_open(path, flags, *args, **kwargs)
            if path == "manifest.json" and not swapped:
                swapped = True
                manifest.rename(displaced)
                manifest.symlink_to(outside_path)
            return descriptor

        with mock.patch("os.open", side_effect=swapping_open):
            raw = module._read_repo_file(
                self.repo,
                "bench/manifest.json",
                max_bytes=module.MAX_MANIFEST_BYTES,
            )

        self.assertTrue(swapped)
        self.assertEqual(json.loads(raw)["evaluated_release"], "0.4.0")

    def test_manifest_requires_publication_identity_fields(self):
        cases = (
            ("wrong kind", lambda item: item.__setitem__("kind", "other")),
            ("wrong schema", lambda item: item.__setitem__("schema_version", 2)),
            ("missing release", lambda item: item.pop("evaluated_release")),
            ("empty release", lambda item: item.__setitem__("evaluated_release", "")),
            ("missing provider", lambda item: item["provenance"].pop("provider")),
            ("empty provider", lambda item: item["provenance"].__setitem__("provider", "")),
            ("missing report", lambda item: item.pop("report")),
            ("missing commit", lambda item: item["provenance"].pop("commit")),
            ("non-hex commit", lambda item: item["provenance"].__setitem__("commit", "g" * 40)),
            ("short commit", lambda item: item["provenance"].__setitem__("commit", "a" * 39)),
            ("uppercase commit", lambda item: item["provenance"].__setitem__("commit", "A" * 40)),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                manifest = self.benchmark_manifest()
                mutate(manifest)
                self.write_benchmark_manifest(manifest)
                with self.assertRaises(ReceiptError):
                    build_receipt(self.repo, Path("bench/manifest.json"))

    def test_manifest_accepts_full_sha256_provenance_commit(self):
        manifest = self.benchmark_manifest()
        manifest["provenance"]["commit"] = "b" * 64
        self.write_benchmark_manifest(manifest)

        benchmark = build_receipt(
            self.repo, Path("bench/manifest.json")
        )["benchmark"]

        self.assertEqual(benchmark["provenance_commit"], "b" * 64)

    def test_manifest_languages_are_unique_nonempty_and_match_artifacts(self):
        cases = (
            (
                "duplicate required language",
                lambda item: item["publication"]["required_languages"].append("rust"),
            ),
            (
                "empty required language",
                lambda item: item["publication"]["required_languages"].__setitem__(0, ""),
            ),
            (
                "non-string required language",
                lambda item: item["publication"]["required_languages"].__setitem__(0, 1),
            ),
            (
                "duplicate artifact language",
                lambda item: item["artifacts"][1].__setitem__(
                    "language", item["artifacts"][0]["language"]
                ),
            ),
            (
                "empty artifact language",
                lambda item: item["artifacts"][0].__setitem__("language", ""),
            ),
            (
                "mismatched language sets",
                lambda item: item["artifacts"][0].__setitem__("language", "swift"),
            ),
        )
        for name, mutate in cases:
            with self.subTest(name=name):
                manifest = self.benchmark_manifest()
                mutate(manifest)
                self.write_benchmark_manifest(manifest)
                with self.assertRaises(ReceiptError):
                    build_receipt(self.repo, Path("bench/manifest.json"))

    def test_referenced_paths_must_be_normalized(self):
        invalid = (
            "/tmp/outside",
            "../outside",
            "bench/../outside",
            "bench\\artifact.json",
            "bench//artifact.json",
            "bench/./artifact.json",
            "",
        )
        for field in ("artifact", "dataset", "report"):
            for value in invalid:
                with self.subTest(field=field, value=value):
                    manifest = self.benchmark_manifest()
                    if field == "artifact":
                        manifest["artifacts"][0]["path"] = value
                    elif field == "dataset":
                        manifest["artifacts"][0]["dataset"]["path"] = value
                    else:
                        manifest["report"]["path"] = value
                    self.write_benchmark_manifest(manifest)
                    with self.assertRaises(ReceiptError):
                        build_receipt(self.repo, Path("bench/manifest.json"))

    def test_referenced_paths_reject_symlinks_and_non_regular_files(self):
        manifest = self.benchmark_manifest()
        artifact = Path(manifest["artifacts"][0]["path"])
        target = self.repo / artifact
        link = target.with_name("artifact-link.json")
        link.symlink_to(target.name)
        linked_parent = self.repo / "bench" / "linked-artifacts"
        linked_parent.symlink_to("artifacts", target_is_directory=True)

        cases = (
            ("symlink file", "bench/artifacts/artifact-link.json"),
            ("symlink parent", f"bench/linked-artifacts/{target.name}"),
            ("directory", "bench/artifacts"),
        )
        for name, value in cases:
            with self.subTest(name=name):
                candidate = copy.deepcopy(manifest)
                candidate["artifacts"][0]["path"] = value
                self.write_benchmark_manifest(candidate)
                with self.assertRaises(ReceiptError):
                    build_receipt(self.repo, Path("bench/manifest.json"))

    def benchmark_manifest(self):
        languages = ["typescript", "rust", "go", "Python", "Java"]
        artifacts = []
        for index, language in enumerate(languages):
            slug = language.lower()
            artifact = self.repo / "bench" / "artifacts" / f"{slug}.json"
            dataset = self.repo / "bench" / "datasets" / f"{slug}.jsonl"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            dataset.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text("{}\n")
            dataset.write_text("{}\n")
            artifacts.append({
                "bytes": 3,
                "dataset": {"path": f"bench/datasets/{slug}.jsonl", "sha256": "b" * 64},
                "language": language,
                "path": f"bench/artifacts/{slug}.json",
                "rows": index + 1,
                "sha256": "c" * 64,
                "source": {"bytes": 3, "sha256": "d" * 64},
            })
        report = self.repo / "bench" / "report.md"
        report.write_text("# Report\n")
        return {
            "schema_version": 1,
            "kind": "evergreen-benchmark-decision-publication",
            "evaluated_release": "0.4.0",
            "projection": {
                "name": "structured-decisions",
                "version": 1,
                "omitted_fields": ["code", "doc", "func", "missed_angle", "reason", "why"],
            },
            "publication": {
                "coverage_threshold": 0.99,
                "required_languages": languages,
            },
            "provenance": {
                "cli_version": "codex-cli test",
                "commit": "a" * 40,
                "judge_sha256": "e" * 64,
                "provider": "codex",
                "settings_sha256": "f" * 64,
                "skill_sha256": "1" * 64,
                "tree": "2" * 40,
            },
            "artifacts": artifacts,
            "report": {"path": "bench/report.md", "sha256": "3" * 64},
        }

    def write_benchmark_manifest(self, manifest):
        path = self.repo / "bench" / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest))
        self.git("add", "bench/manifest.json")
        self.git("commit", "-qm", "record benchmark manifest")

    def repository_snapshot(self, repo=None):
        repo = self.repo if repo is None else Path(repo)
        git_dir = Path(
            self.run_git(repo, "rev-parse", "--absolute-git-dir")
        ).resolve()
        git_common_dir = Path(
            self.run_git(
                repo,
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            )
        ).resolve()

        def tree(path):
            root_metadata = path.lstat()
            entries = {
                ".": (
                    stat.S_IFMT(root_metadata.st_mode),
                    stat.S_IMODE(root_metadata.st_mode),
                    None,
                )
            }
            for item in sorted(path.rglob("*")):
                relative = item.relative_to(path).as_posix()
                metadata = item.lstat()
                kind = stat.S_IFMT(metadata.st_mode)
                mode = stat.S_IMODE(metadata.st_mode)
                if stat.S_ISREG(metadata.st_mode):
                    value = item.read_bytes()
                elif stat.S_ISLNK(metadata.st_mode):
                    value = os.readlink(item)
                else:
                    value = None
                entries[relative] = (kind, mode, value)
            return entries

        return {
            "worktree": tree(repo),
            "git_dir_path": str(git_dir),
            "git_dir": tree(git_dir),
            "git_common_dir_path": str(git_common_dir),
            "git_common_dir": tree(git_common_dir),
        }
