import hashlib
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

from eval.bench import frozen_run


class FrozenRunSafetyTests(unittest.TestCase):
    def test_v2_requires_split_manifest_and_validates_context_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "java.jsonl"
            row = {
                "id": "org/repo/f#1", "language": "Java", "func": "f",
                "code": "return 1", "doc": "returns one", "label": "consistent",
                "category": None,
            }
            dataset.write_text(json.dumps(row) + "\n")
            manifest = root / "split.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"sha256": "a" * 64, "language": "Java"}],
                "rows": [{"id": row["id"], "dataset_sha256": "a" * 64,
                          "project": "org/repo", "split": "dev"}],
            }))
            manifest_digest = hashlib.sha256(manifest.read_bytes()).hexdigest()

            with self.assertRaisesRegex(ValueError, "v2 requires"):
                frozen_run.run_policy(dataset, [row], "v2", None, None, "none")
            with self.assertRaisesRegex(ValueError, "context protocol"):
                frozen_run.run_policy(
                    dataset, [row], "v2", manifest, "dev", "java-git-window-v1"
                )
            with self.assertRaisesRegex(ValueError, "Java resolver v2"):
                frozen_run.run_policy(dataset, [row], "v2", manifest, "dev", "none")

            row["context"] = {
                "status": "unavailable", "protocol": "java-git-window-v1",
                "reason": "mirror-unavailable",
            }
            policy = frozen_run.run_policy(
                dataset, [row], "v2", manifest, "dev", "java-git-window-v1"
            )

        self.assertEqual(policy["resolver"], "v2")
        self.assertEqual(policy["split"], "dev")
        self.assertEqual(policy["context_protocol"], "java-git-window-v1")
        self.assertEqual(policy["split_manifest_sha256"], manifest_digest)

    def test_split_policy_rejects_wrong_assignment_and_undeclared_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "data.jsonl"
            row = {"id": "org/repo/f#1", "language": "Python"}
            dataset.write_text(json.dumps(row))
            manifest = root / "split.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"sha256": "a" * 64, "language": "Java"}],
                "rows": [{"id": row["id"], "dataset_sha256": "a" * 64,
                          "project": "org/repo", "split": "holdout"}],
            }))
            with self.assertRaisesRegex(ValueError, "declared split"):
                frozen_run.run_policy(dataset, [row], "v2", manifest, "dev", "none")
            with self.assertRaisesRegex(ValueError, "not declared"):
                frozen_run.run_policy(
                    dataset, [{**row, "context": {}}], "v1", None, None, "none"
                )

    def test_managed_plugin_checkout_is_refused(self):
        for repo in (
            Path("/Users/me/.claude/plugins/marketplaces/evergreen"),
            Path("/opt/custom-config/plugins/marketplaces/evergreen"),
            Path("/opt/custom-config/plugins/cache/evergreen/0.4.0"),
        ):
            with self.subTest(repo=repo), self.assertRaisesRegex(
                ValueError, "managed plugin checkout"
            ):
                frozen_run.validate_locations(repo, Path("/Users/me/archive"))

    def test_archive_must_be_absolute_and_outside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory).resolve()
            with self.assertRaisesRegex(ValueError, "absolute"):
                frozen_run.validate_locations(repo, Path("archive"))
            with self.assertRaisesRegex(ValueError, "outside"):
                frozen_run.validate_locations(repo, repo / "archive")

    def test_remote_must_contain_exact_commit_as_a_ref_tip(self):
        self.assertIsNone(frozen_run.require_pushed("a" * 40, f"{'a' * 40}\trefs/heads/main\n"))
        with self.assertRaisesRegex(ValueError, "not pushed"):
            frozen_run.require_pushed("a" * 40, f"{'b' * 40}\trefs/heads/main\n")

    def test_guard_rejects_identity_change_and_low_disk(self):
        expected = {"commit": "c", "tree": "t", "dirty": False}
        self.assertIsNone(frozen_run.guard_reason(expected, expected, 10, 5))
        self.assertIn("identity", frozen_run.guard_reason(
            expected, {**expected, "tree": "other"}, 10, 5
        ))
        self.assertIn("disk", frozen_run.guard_reason(expected, expected, 4, 5))

    def test_workspace_token_detects_same_path_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            repo = parent / "repo"
            (repo / ".git").mkdir(parents=True)
            before = frozen_run.workspace_token(repo)
            repo.rename(parent / "old")
            (repo / ".git").mkdir(parents=True)
            after = frozen_run.workspace_token(repo)
        self.assertNotEqual(before, after)

    def test_archive_lock_allows_only_one_language_lane(self):
        with tempfile.TemporaryDirectory() as directory:
            lock_path = Path(directory) / "global.lock"
            first = frozen_run.acquire_lock(lock_path)
            try:
                with self.assertRaisesRegex(ValueError, "another frozen benchmark lane"):
                    frozen_run.acquire_lock(lock_path)
            finally:
                first.close()

    def test_archive_failure_stops_the_paid_child(self):
        stopped = []
        process = SimpleNamespace(poll=lambda: None, returncode=None)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "artifact.json"
            output.write_text("present")
            with self.assertRaisesRegex(OSError, "archive full"):
                frozen_run.monitor_process(
                    process=process,
                    output=output,
                    archive=Path(directory) / "archive",
                    expected_metadata={"git": {"commit": "a" * 40}},
                    expected_identity={"commit": "c"},
                    expected_workspace=(1,),
                    minimum_free=1,
                    poll_seconds=0,
                    archive_fn=lambda *_args: (_ for _ in ()).throw(OSError("archive full")),
                    identity_fn=lambda: {"commit": "c"},
                    workspace_fn=lambda: (1,),
                    free_fn=lambda: 10,
                    stop_fn=lambda child: stopped.append(child),
                    sleep_fn=lambda _seconds: None,
                )
        self.assertEqual(stopped, [process])


class FrozenRunArchiveTests(unittest.TestCase):
    def artifact(self, commit, rows, concurrency=4):
        return {
            "schema_version": 1,
            "metadata": {
                "git": {"commit": commit, "dirty": False},
                "settings": {"concurrency": concurrency},
            },
            "timing": {"started_at": "2026-01-01T00:00:00Z", "elapsed_seconds": 1},
            "rows": [{
                "id": str(index), "language": "python", "label": "consistent",
                "category": None,
                "got": {"final_status": "complete", "final_verdict": "consistent"},
            } for index in range(rows)],
        }

    def test_archives_content_addressed_versions_and_restores_highest_valid(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "live.json"
            archive = root / "archive"
            one = self.artifact(commit, 1)
            live.write_text(json.dumps(one))
            first = frozen_run.archive_checkpoint(live, archive, one["metadata"])
            live.write_text(json.dumps(self.artifact(commit, 3)))
            third = frozen_run.archive_checkpoint(live, archive, one["metadata"])

            self.assertIn("rows-1", first.name)
            self.assertIn(hashlib.sha256(first.read_bytes()).hexdigest(), first.name)
            self.assertIn("rows-3", third.name)
            live.unlink()
            restored = frozen_run.restore_latest(live, archive, one["metadata"])

            self.assertEqual(restored, third)
            self.assertEqual(len(json.loads(live.read_text())["rows"]), 3)

    def test_restore_skips_tampered_and_wrong_commit_archives(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "live.json"
            archive = root / "archive"
            one = self.artifact(commit, 1)
            live.write_text(json.dumps(one))
            valid = frozen_run.archive_checkpoint(live, archive, one["metadata"])
            live.write_text(json.dumps(self.artifact(commit, 2)))
            tampered = frozen_run.archive_checkpoint(live, archive, one["metadata"])
            tampered.write_text("tampered")
            live.unlink()

            restored = frozen_run.restore_latest(live, archive, one["metadata"])

            self.assertEqual(restored, valid)
            self.assertEqual(len(json.loads(live.read_text())["rows"]), 1)
            wrong_live = root / "wrong.json"
            self.assertIsNone(frozen_run.restore_latest(
                wrong_live, archive, self.artifact("b" * 40, 1)["metadata"]
            ))

    def test_peer_checkpoint_archives_and_restores_under_raw_peer_schema(self):
        from eval import peers

        commit = "a" * 40
        metadata = {"git": {"commit": commit}, "settings": {"peer_id": "direct-baseline"}}
        private = [{
            "id": "private-1", "language": "python", "code": "return 1",
            "documentation": "returns one", "label": "consistent",
        }]
        request = peers.freeze_request(private, b"s" * 32)
        decision = {"opaque_id": request["rows"][0]["opaque_id"],
                    "decision": "consistent"}
        document = peers.run_document(
            metadata, request, [decision], started_at="2026-07-14T12:00:00Z",
            elapsed_seconds=1.0,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "peer.json"
            archive = root / "archive"
            live.write_text(json.dumps(document))
            frozen_run.archive_checkpoint(
                live, archive, metadata, peer_request=request,
            )
            live.unlink()
            restored = frozen_run.restore_latest(
                live, archive, metadata, peer_request=request,
            )
            restored_document = json.loads(live.read_text())
        self.assertIsNotNone(restored)
        self.assertEqual(restored_document, document)

    def test_restore_skips_higher_same_commit_with_incompatible_settings(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "live.json"
            archive = root / "archive"
            compatible = self.artifact(commit, 1, concurrency=4)
            live.write_text(json.dumps(compatible))
            expected = frozen_run.archive_checkpoint(
                live, archive, compatible["metadata"]
            )
            incompatible = self.artifact(commit, 5, concurrency=8)
            live.write_text(json.dumps(incompatible))
            frozen_run.archive_checkpoint(live, archive, incompatible["metadata"])
            live.unlink()

            restored = frozen_run.restore_latest(
                live, archive, compatible["metadata"]
            )

            self.assertEqual(restored, expected)
            self.assertEqual(len(json.loads(live.read_text())["rows"]), 1)

    def test_restore_rejects_rows_that_do_not_match_hashed_dataset(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "live.json"
            archive = root / "archive"
            value = self.artifact(commit, 1)
            live.write_text(json.dumps(value))
            frozen_run.archive_checkpoint(live, archive, value["metadata"])
            live.unlink()
            dataset_rows = [{
                **{key: item for key, item in value["rows"][0].items() if key != "got"},
                "label": "inconsistent",
            }]

            restored = frozen_run.restore_latest(
                live, archive, value["metadata"], dataset_rows=dataset_rows
            )

            self.assertIsNone(restored)
            self.assertFalse(live.exists())

    def test_corrupt_live_is_quarantined_before_valid_archive_restore(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "live.json"
            archive = root / "archive"
            compatible = self.artifact(commit, 2)
            live.write_text(json.dumps(compatible))
            frozen_run.archive_checkpoint(live, archive, compatible["metadata"])
            live.write_text("corrupt")

            restored = frozen_run.prepare_output(
                live, archive, compatible["metadata"]
            )

            self.assertIsNotNone(restored)
            self.assertEqual(len(json.loads(live.read_text())["rows"]), 2)
            self.assertEqual(len(list((archive / commit / "quarantine").glob("*.json"))), 1)

    def test_commit_directory_symlink_cannot_redirect_archive_write(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive"
            outside = root / "outside"
            archive.mkdir()
            outside.mkdir()
            (archive / commit).symlink_to(outside, target_is_directory=True)
            live = root / "live.json"
            value = self.artifact(commit, 1)
            live.write_text(json.dumps(value))

            with self.assertRaisesRegex(ValueError, "real directory"):
                frozen_run.archive_checkpoint(live, archive, value["metadata"])

            self.assertEqual(list(outside.iterdir()), [])

    def test_quarantine_collision_never_deletes_live_copy(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive"
            live = root / "live.json"
            live.write_text("corrupt")
            metadata = self.artifact(commit, 1)["metadata"]
            digest = hashlib.sha256(b"corrupt").hexdigest()
            collision = archive / commit / "quarantine" / f"live.{digest}.json"
            collision.parent.mkdir(parents=True)
            collision.write_text("different")

            with self.assertRaisesRegex(ValueError, "different bytes"):
                frozen_run.quarantine_live(live, archive, metadata)

            self.assertTrue(live.exists())
            self.assertEqual(live.read_text(), "corrupt")

    def test_archive_collision_never_downgrades_valid_live_checkpoint(self):
        commit = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "archive"
            live = root / "live.json"
            one = self.artifact(commit, 1)
            live.write_text(json.dumps(one))
            frozen_run.archive_checkpoint(live, archive, one["metadata"])
            three = self.artifact(commit, 3)
            live.write_text(json.dumps(three))
            raw = live.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            collision = archive / commit / f"live.rows-3.{digest}.json"
            collision.write_text("different")

            with self.assertRaisesRegex(ValueError, "different bytes"):
                frozen_run.prepare_output(live, archive, one["metadata"])

            self.assertEqual(len(json.loads(live.read_text())["rows"]), 3)


class FrozenRunMainTests(unittest.TestCase):
    def test_main_wires_preflight_handshake_restore_monitor_and_global_lock(self):
        commit = "a" * 40
        identity = {"commit": commit, "tree": "t", "dirty": False}
        metadata = {"git": identity, "settings": {"concurrency": 4}}
        process = SimpleNamespace(returncode=0)
        lock = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            here = repo / "eval" / "bench"
            here.mkdir(parents=True)
            (repo / ".git").mkdir()
            dataset = here / "one.jsonl"
            dataset.write_text("fixture")
            archive = root / "archive"
            resolved_archive = archive.resolve()
            with mock.patch.object(frozen_run, "REPO", repo), \
                 mock.patch.object(frozen_run, "HERE", here), \
                 mock.patch.object(frozen_run, "git_identity", return_value=identity), \
                 mock.patch.object(
                     frozen_run, "artifact_metadata", return_value=metadata
                 ) as metadata_call, \
                 mock.patch.object(frozen_run, "load_dataset", return_value=(b"fixture", [
                     {"language": "python"}
                 ])), \
                 mock.patch.object(frozen_run, "_remote_refs", return_value=(
                     f"{commit}\trefs/heads/main\n"
                 )), \
                 mock.patch.object(frozen_run, "acquire_lock", return_value=lock) as acquire, \
                 mock.patch.object(frozen_run, "prepare_output") as prepare, \
                 mock.patch.object(frozen_run.subprocess, "Popen", return_value=process) as popen, \
                 mock.patch.object(frozen_run, "monitor_process", return_value=0) as monitor, \
                 mock.patch.object(frozen_run.shutil, "disk_usage", return_value=(
                     SimpleNamespace(free=20 * 1024 ** 3)
                 )):
                status = frozen_run.main([
                    "--dataset", str(dataset), "--archive-dir", str(archive),
                ])

        self.assertEqual(status, 0)
        acquire.assert_called_once_with(
            Path("/tmp") / f"evergreen-benchmark-{os.getuid()}.lock"
        )
        prepare.assert_called_once_with(
            here / "out" / "bench-one-trial-codex-gpt-5.6-sol.json",
            resolved_archive,
            metadata,
            dataset_rows=[{"language": "python"}],
        )
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["EVAL_FROZEN_ARCHIVE_DIR"], str(resolved_archive))
        self.assertIn("EVAL_FROZEN_FD", environment)
        self.assertIn("EVAL_FROZEN_TOKEN_SHA256", environment)
        self.assertEqual(environment["EVAL_RESOLVER"], "v1")
        self.assertEqual(environment["EVAL_CONTEXT_PROTOCOL"], "none")
        self.assertEqual(metadata_call.call_args.args[2]["resolver"], "v1")
        self.assertEqual(metadata_call.call_args.args[2]["context_protocol"], "none")
        self.assertIsNone(metadata_call.call_args.args[2]["split_manifest_sha256"])
        self.assertIsNone(metadata_call.call_args.args[2]["split"])
        self.assertEqual(len(popen.call_args.kwargs["pass_fds"]), 1)
        self.assertEqual(monitor.call_args.kwargs["expected_metadata"], metadata)
        lock.close.assert_called_once_with()

    def test_peer_main_spawns_single_pass_runner_with_matching_settings_and_two_secrets(self):
        commit = "a" * 40
        identity = {"commit": commit, "tree": "t", "dirty": False}
        metadata = {"git": identity, "settings": {"peer_id": "direct-baseline"}}
        process = SimpleNamespace(returncode=0)
        lock = mock.Mock()
        row = {
            "id": "private-1", "language": "python", "func": "f",
            "code": "def f(): return 1", "doc": "returns one",
            "label": "consistent", "category": None,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            here = repo / "eval" / "bench"
            here.mkdir(parents=True)
            (repo / ".git").mkdir()
            dataset = here / "one.jsonl"
            dataset.write_text(json.dumps(row) + "\n")
            manifest = root / "peers.json"
            manifest.write_text("fixture")
            key = root / "peer.key"
            key.write_bytes(b"k" * 32)
            key.chmod(0o600)
            archive = root / "archive"
            peer = {
                "peer_id": "direct-baseline",
                "peer_manifest_sha256": "1" * 64,
                "peer_config_sha256": "2" * 64,
                "peer_source_sha256": "3" * 64,
                "peer_source": {"kind": "protocol"},
            }
            with mock.patch.object(frozen_run, "REPO", repo), \
                 mock.patch.object(frozen_run, "HERE", here), \
                 mock.patch.object(frozen_run, "git_identity", return_value=identity), \
                 mock.patch.object(frozen_run, "artifact_metadata", return_value=metadata), \
                 mock.patch.object(frozen_run, "load_dataset", return_value=(b"fixture", [row])), \
                 mock.patch.object(frozen_run, "peer_policy", return_value=peer), \
                 mock.patch.object(frozen_run, "_remote_refs", return_value=(
                     f"{commit}\trefs/heads/main\n"
                 )), \
                 mock.patch.object(frozen_run, "acquire_lock", return_value=lock), \
                 mock.patch.object(frozen_run, "prepare_output") as prepare, \
                 mock.patch.object(frozen_run.subprocess, "Popen", return_value=process) as popen, \
                 mock.patch.object(frozen_run, "monitor_process", return_value=0) as monitor, \
                 mock.patch.object(frozen_run.shutil, "disk_usage", return_value=(
                     SimpleNamespace(free=20 * 1024 ** 3)
                 )):
                status = frozen_run.main([
                    "--dataset", str(dataset), "--archive-dir", str(archive),
                    "--peer-manifest", str(manifest), "--peer-id", "direct-baseline",
                    "--peer-key-file", str(key),
                ])

        self.assertEqual(status, 0)
        self.assertEqual(Path(popen.call_args.args[0][1]).name, "run_peer.py")
        self.assertEqual(len(popen.call_args.kwargs["pass_fds"]), 2)
        environment = popen.call_args.kwargs["env"]
        child_settings = json.loads(environment["EVAL_PEER_SETTINGS_JSON"])
        self.assertEqual(child_settings["peer_id"], "direct-baseline")
        self.assertEqual(child_settings["model"], "gpt-5.6-sol")
        self.assertNotIn("models", child_settings)
        self.assertIn("EVAL_PEER_KEY_FD", environment)
        prepared = prepare.call_args
        self.assertEqual(prepared.args[0].name,
                         "peer-one-codex-gpt-5.6-sol-direct-baseline.json")
        self.assertIn("peer_request", prepared.kwargs)
        self.assertIs(monitor.call_args.kwargs["expected_metadata"], metadata)
        self.assertIn("archive_fn", monitor.call_args.kwargs)
        lock.close.assert_called_once_with()

    def test_peer_key_must_be_external_private_regular_and_exactly_32_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            key = root / "peer.key"
            key.write_bytes(b"k" * 32)
            key.chmod(0o600)
            self.assertEqual(frozen_run.load_peer_key(key, repo), b"k" * 32)
            key.chmod(0o644)
            with self.assertRaisesRegex(ValueError, "private"):
                frozen_run.load_peer_key(key, repo)
            key.chmod(0o600)
            link = root / "key-link"
            link.symlink_to(key)
            with self.assertRaisesRegex(ValueError, "regular"):
                frozen_run.load_peer_key(link, repo)
            inside = repo / "peer.key"
            inside.write_bytes(b"k" * 32)
            inside.chmod(0o600)
            with self.assertRaisesRegex(ValueError, "outside"):
                frozen_run.load_peer_key(inside, repo)


if __name__ == "__main__":
    unittest.main()
