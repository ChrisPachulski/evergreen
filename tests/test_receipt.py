import copy
import json
import os
import subprocess
import sys
import tempfile
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
        self.assertEqual(first["repository"]["name"], "repo")
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

    def test_detached_head_has_no_branch_or_upstream(self):
        self.git("checkout", "-q", "--detach")

        repository = build_receipt(self.repo)["repository"]

        self.assertIsNone(repository["branch"])
        self.assertTrue(repository["detached"])
        self.assertIsNone(repository["upstream"])
        self.assertIsNone(repository["ahead"])
        self.assertIsNone(repository["behind"])

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
            "file:///tmp/repo.git",
            "/tmp/repo.git",
            "example.invalid:owner/repo.git",
        )
        for remote in remotes:
            with self.subTest(remote=remote):
                self.git("remote", "set-url", "origin", remote)
                self.assertEqual(
                    build_receipt(self.repo)["repository"]["origin"], remote
                )

    def test_stub_git_timeout_and_output_limit_are_errors(self):
        from evergreen import receipt as module

        stub = self.repo.parent / "git-stub"
        stub.write_text(
            f"#!{sys.executable}\n"
            "import os, sys, time\n"
            "mode = os.environ['RECEIPT_STUB_MODE']\n"
            "if mode == 'timeout':\n"
            "    time.sleep(1)\n"
            "else:\n"
            f"    sys.stdout.buffer.write(b'x' * ({module.MAX_GIT_OUTPUT_BYTES} + 1))\n"
        )
        stub.chmod(0o755)

        with mock.patch.object(module, "_GIT_EXECUTABLE", str(stub)), \
                mock.patch.object(module, "GIT_TIMEOUT_SECONDS", 0.01), \
                mock.patch.dict(os.environ, {"RECEIPT_STUB_MODE": "timeout"}), \
                self.assertRaisesRegex(ReceiptError, "timed out"):
            build_receipt(self.repo)

        with mock.patch.object(module, "_GIT_EXECUTABLE", str(stub)), \
                mock.patch.dict(os.environ, {"RECEIPT_STUB_MODE": "output"}), \
                self.assertRaisesRegex(ReceiptError, "too much output"):
            build_receipt(self.repo)

    def test_receipt_does_not_change_repository_bytes(self):
        (self.repo / "untracked").write_text("leave me\n")

        before = self.repository_snapshot()
        build_receipt(self.repo)
        after = self.repository_snapshot()

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
            "languages": ["Java", "Python", "go", "rust", "typescript"],
            "manifest": "bench/manifest.json",
            "provenance_commit": "a" * 40,
            "provider": "codex",
            "report": "bench/report.md",
        })

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

    def repository_snapshot(self):
        git_dir = self.repo / ".git"

        def files_below(path):
            if not path.exists():
                return {}
            return {
                item.relative_to(git_dir).as_posix(): item.read_bytes()
                for item in path.rglob("*")
                if item.is_file()
            }

        worktree = {
            item.relative_to(self.repo).as_posix(): item.read_bytes()
            for item in self.repo.rglob("*")
            if item.is_file() and git_dir not in item.parents
        }
        return {
            "HEAD": (git_dir / "HEAD").read_bytes(),
            "index": (git_dir / "index").read_bytes(),
            "refs": files_below(git_dir / "refs"),
            "worktree": worktree,
        }
