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
    def setUp(self):
        self.real_head_reader = frozen_run.head_bound_bytes
        self.head_reader = mock.patch.object(
            frozen_run, "head_bound_bytes",
            side_effect=lambda path, _maximum, _label, *_rest: Path(path).read_bytes(),
        )
        self.head_reader.start()

    def tearDown(self):
        self.head_reader.stop()

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
            dataset_digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
            manifest = root / "split.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"sha256": dataset_digest, "language": "Java"}],
                "rows": [{"id": row["id"], "dataset_sha256": dataset_digest,
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

    def test_v3_requires_a_positive_provider_attempt_ceiling(self):
        dataset = Path("data.jsonl")
        rows = [{"id": "org/repo/f#1", "language": "python"}]
        for bad_ceiling in (None, 0, -1, "50", 5.0, True):
            with self.subTest(bad_ceiling=bad_ceiling), self.assertRaisesRegex(
                ValueError, "max-provider-attempts"
            ):
                frozen_run.run_policy(
                    dataset, rows, "v3", None, None, "none",
                    max_provider_attempts=bad_ceiling,
                )
        # A positive int is accepted and frozen into the returned settings only for v3.
        policy = frozen_run.run_policy(
            dataset, rows, "v3", None, None, "none", max_provider_attempts=50,
        )
        self.assertEqual(policy["max_provider_attempts"], 50)

    def test_ceiling_is_rejected_for_v1_and_v2(self):
        dataset = Path("data.jsonl")
        rows = [{"id": "org/repo/f#1", "language": "python"}]
        for resolver in ("v1", "v2"):
            with self.subTest(resolver=resolver), self.assertRaisesRegex(
                ValueError, "requires resolver v3"
            ):
                frozen_run.run_policy(
                    dataset, rows, resolver, None, None, "none",
                    max_provider_attempts=50,
                )

    def test_v1_and_v2_settings_never_carry_a_max_provider_attempts_key(self):
        dataset = Path("data.jsonl")
        rows = [{"id": "org/repo/f#1", "language": "python"}]
        policy = frozen_run.run_policy(dataset, rows, "v1", None, None, "none")
        self.assertNotIn("max_provider_attempts", policy)

    def test_split_policy_rejects_dataset_bytes_not_bound_by_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "data.jsonl"
            row = {"id": "org/repo/f#1", "language": "Python"}
            dataset.write_text(json.dumps(row) + "\n")
            manifest = root / "split.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"sha256": "a" * 64, "language": "Python"}],
                "rows": [{"id": row["id"], "dataset_sha256": "a" * 64,
                          "project": "org/repo", "split": "dev"}],
            }))

            with self.assertRaisesRegex(ValueError, "dataset bytes"):
                frozen_run.run_policy(dataset, [row], "v2", manifest, "dev", "none")

    def test_split_policy_rejects_wrong_declared_dataset_language(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "data.jsonl"
            row = {"id": "org/repo/f#1", "language": "Python"}
            dataset.write_text(json.dumps(row) + "\n")
            digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
            manifest = root / "split.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"sha256": digest, "language": "Java"}],
                "rows": [{"id": row["id"], "dataset_sha256": digest,
                          "project": "org/repo", "split": "dev"}],
            }))

            with self.assertRaisesRegex(ValueError, "language"):
                frozen_run.run_policy(dataset, [row], "v2", manifest, "dev", "none")

    def test_non_java_v2_requires_complete_screen_ancestry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "data.jsonl"
            row = {"id": "org/repo/f#1", "language": "Python"}
            dataset.write_text(json.dumps(row) + "\n")
            digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
            manifest = root / "split.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"sha256": digest, "language": "Python"}],
                "rows": [{"id": row["id"], "dataset_sha256": digest,
                          "project": "org/repo", "split": "dev"}],
            }))

            with self.assertRaisesRegex(ValueError, "screen selection ancestry"):
                frozen_run.run_policy(dataset, [row], "v2", manifest, "dev", "none")

    def test_non_java_v2_recomputes_and_binds_screen_selection(self):
        from eval.bench import bind_subset, validate_labels

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {"id": "org/a/f#1-old", "func": "f", "code": "return 1",
                 "doc": "returns two", "label": "inconsistent", "category": None,
                 "language": "Python"},
                {"id": "org/b/g#1-new", "func": "g", "code": "return 1",
                 "doc": "returns one", "label": "consistent", "category": None,
                 "language": "Python"},
            ]
            parent = root / "eligible.jsonl"
            parent.write_text("".join(json.dumps(row) + "\n" for row in rows))
            parent_digest = hashlib.sha256(parent.read_bytes()).hexdigest()
            parent_manifest = root / "eligible-manifest.json"
            parent_manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"sha256": parent_digest, "language": "Python"}],
                "rows": [{"id": row["id"], "dataset_sha256": parent_digest,
                          "project": row["id"].split("/")[0] + "/" +
                          row["id"].split("/")[1], "split": "dev"}
                         for row in rows],
            }))
            binding = validate_labels._vote_binding(
                parent.read_bytes(), cli_version="claude test",
                cli_executable_sha256="a" * 64,
            )
            ledger = root / "screen.votes.json"
            validate_labels._write_votes(ledger, binding, {
                rows[0]["id"]: {model: "inconsistent"
                                for model in validate_labels.ANNOTATORS},
                rows[1]["id"]: {model: "inconsistent"
                                for model in validate_labels.ANNOTATORS},
            })
            dataset = root / "validated.jsonl"
            dataset.write_text(json.dumps(rows[0]) + "\n")
            manifest_document = bind_subset.build_manifest(
                dataset, [parent], parent_manifest, "dev", vote_ledger=ledger
            )
            manifest = root / "validated-manifest.json"
            manifest.write_bytes(bind_subset.manifest_bytes(manifest_document))
            receipt_document = bind_subset.build_screen_receipt(
                dataset, parent, parent_manifest, ledger, "dev", manifest_document
            )
            receipt = root / "selection-receipt.json"
            receipt.write_bytes(bind_subset.receipt_bytes(receipt_document))

            with mock.patch.object(
                bind_subset, "build_manifest",
                side_effect=AssertionError("reopened selection paths"),
            ), mock.patch.object(
                bind_subset, "build_screen_receipt",
                side_effect=AssertionError("reopened selection paths"),
            ):
                policy = frozen_run.run_policy(
                    dataset, [rows[0]], "v2", manifest, "dev", "none",
                    parent, parent_manifest, ledger, receipt,
                )

            self.assertEqual(
                policy["selection_receipt_sha256"],
                hashlib.sha256(receipt.read_bytes()).hexdigest(),
            )

    def test_head_bound_manifest_rejects_external_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "external.json"
            path.write_text("{}")
            with self.assertRaisesRegex(ValueError, "inside the repository"):
                self.real_head_reader(path, 1024, "split manifest")

    def test_head_bound_manifest_rejects_untracked_repository_path(self):
        with tempfile.TemporaryDirectory(dir=frozen_run.REPO) as directory:
            path = Path(directory) / "untracked.json"
            path.write_text("{}")
            with self.assertRaisesRegex(ValueError, "tracked at HEAD"):
                self.real_head_reader(path, 1024, "split manifest")

    def test_artifact_metadata_must_match_split_preflight_dataset_bytes(self):
        with self.assertRaisesRegex(ValueError, "changed after split preflight"):
            frozen_run.require_dataset_binding(
                "a" * 64, {"dataset": {"sha256": "b" * 64}}
            )

    def test_split_policy_rejects_wrong_assignment_and_undeclared_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "data.jsonl"
            row = {"id": "org/repo/f#1", "language": "Python"}
            dataset.write_text(json.dumps(row))
            dataset_digest = hashlib.sha256(dataset.read_bytes()).hexdigest()
            manifest = root / "split.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"sha256": dataset_digest, "language": "Python"}],
                "rows": [{"id": row["id"], "dataset_sha256": dataset_digest,
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
    def test_main_rejects_head_change_across_policy_preflight(self):
        first = {"commit": "a" * 40, "tree": "1", "dirty": False}
        second = {"commit": "b" * 40, "tree": "2", "dirty": False}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            here = repo / "eval" / "bench"
            here.mkdir(parents=True)
            (repo / ".git").mkdir()
            dataset = here / "one.jsonl"
            dataset.write_text("fixture")
            archive = root / "archive"
            with mock.patch.object(frozen_run, "REPO", repo), \
                 mock.patch.object(frozen_run, "HERE", here), \
                 mock.patch.object(
                     frozen_run, "git_identity", side_effect=[first, second]
                 ), \
                 mock.patch.object(
                     frozen_run, "load_dataset",
                     return_value=(b"fixture", [{"language": "python"}]),
                 ), \
                 mock.patch.object(frozen_run, "artifact_metadata") as metadata:
                with self.assertRaisesRegex(ValueError, "identity changed"):
                    frozen_run.main([
                        "--dataset", str(dataset), "--archive-dir", str(archive),
                    ])
        metadata.assert_not_called()

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
        # A v1 run must never carry the v3 ceiling key at all — not even as null — so v1/v2
        # settings, metadata, and artifact filenames stay byte-identical to pre-ceiling runs.
        self.assertNotIn("max_provider_attempts", metadata_call.call_args.args[2])
        self.assertNotIn("EVAL_MAX_PROVIDER_ATTEMPTS", environment)
        self.assertEqual(len(popen.call_args.kwargs["pass_fds"]), 1)
        self.assertEqual(monitor.call_args.kwargs["expected_metadata"], metadata)
        lock.close.assert_called_once_with()

    def test_v3_launch_without_ceiling_fails_before_spawn(self):
        commit = "a" * 40
        identity = {"commit": commit, "tree": "t", "dirty": False}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            here = repo / "eval" / "bench"
            here.mkdir(parents=True)
            (repo / ".git").mkdir()
            dataset = here / "one.jsonl"
            dataset.write_text("fixture")
            archive = root / "archive"
            with mock.patch.object(frozen_run, "REPO", repo), \
                 mock.patch.object(frozen_run, "HERE", here), \
                 mock.patch.object(frozen_run, "git_identity", return_value=identity), \
                 mock.patch.object(frozen_run, "load_dataset", return_value=(b"fixture", [
                     {"language": "python"}
                 ])), \
                 mock.patch.object(frozen_run, "artifact_metadata") as metadata_call, \
                 mock.patch.object(frozen_run.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(ValueError, "max-provider-attempts"):
                    frozen_run.main([
                        "--dataset", str(dataset), "--archive-dir", str(archive),
                        "--resolver", "v3",
                    ])

        metadata_call.assert_not_called()
        popen.assert_not_called()

    def test_main_wires_v3_ceiling_into_settings_metadata_and_environment(self):
        commit = "a" * 40
        identity = {"commit": commit, "tree": "t", "dirty": False}
        metadata = {"git": identity, "settings": {"concurrency": 4, "max_provider_attempts": 50}}
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
                 mock.patch.object(frozen_run, "acquire_lock", return_value=lock), \
                 mock.patch.object(frozen_run, "prepare_output"), \
                 mock.patch.object(frozen_run.subprocess, "Popen", return_value=process) as popen, \
                 mock.patch.object(frozen_run, "monitor_process", return_value=0), \
                 mock.patch.object(frozen_run.shutil, "disk_usage", return_value=(
                     SimpleNamespace(free=20 * 1024 ** 3)
                 )):
                status = frozen_run.main([
                    "--dataset", str(dataset), "--archive-dir", str(archive),
                    "--resolver", "v3", "--max-provider-attempts", "50",
                ])

        self.assertEqual(status, 0)
        self.assertEqual(metadata_call.call_args.args[2]["resolver"], "v3")
        self.assertEqual(metadata_call.call_args.args[2]["max_provider_attempts"], 50)
        environment = popen.call_args.kwargs["env"]
        self.assertEqual(environment["EVAL_RESOLVER"], "v3")
        self.assertEqual(environment["EVAL_MAX_PROVIDER_ATTEMPTS"], "50")

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

    def test_peer_key_path_swap_during_descriptor_read_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            repo.mkdir()
            key = root / "peer.key"
            key.write_bytes(b"a" * 32)
            key.chmod(0o600)
            replacement = root / "replacement"
            replacement.write_bytes(b"b" * 32)
            replacement.chmod(0o600)
            real_read = os.read
            swapped = False

            def swap_then_read(descriptor, size):
                nonlocal swapped
                if not swapped:
                    os.replace(replacement, key)
                    swapped = True
                return real_read(descriptor, size)

            with mock.patch.object(frozen_run.os, "read", side_effect=swap_then_read):
                with self.assertRaisesRegex(ValueError, "changed"):
                    frozen_run.load_peer_key(key, repo)


if __name__ == "__main__":
    unittest.main()
