import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from eval.bench import metrics, runner, trial


def completed(identifier, language, label, category, verdict):
    return {
        "id": identifier,
        "language": language,
        "label": label,
        "category": category,
        "got": {
            "final_status": "complete",
            "final_verdict": verdict,
        },
    }


def clear_execution(attempts=1):
    return {
        "strategy": "cascade-v1", "route": "clear", "logical_calls": 1,
        "provider_attempts": attempts, "attempts_by_tier": {"cheap": attempts, "strong": 0},
        "attempts_by_stage": {"screen": attempts},
    }


def jury_execution(screen=1, snap=1, challenge=1, prongs=3, blindspot=1):
    total = screen + snap + challenge + prongs + blindspot
    return {
        "strategy": "cascade-v1", "route": "jury", "logical_calls": 5,
        "provider_attempts": total,
        "attempts_by_tier": {"cheap": screen + challenge + prongs, "strong": snap + blindspot},
        "attempts_by_stage": {
            "screen": screen, "snap": snap, "challenge": challenge, "prongs": prongs,
            "blindspot": blindspot,
        },
    }


def completed_v3(identifier, language, label, category, verdict, got_category, execution):
    return {
        "id": identifier,
        "language": language,
        "label": label,
        "category": category,
        "got": {
            "final_status": "complete", "semantic_status": "decided",
            "final_verdict": verdict, "category": got_category, "execution": execution,
        },
    }


class ArtifactMetadataTests(unittest.TestCase):
    def test_row_validation_rejects_invalid_and_unhashable_categories(self):
        from eval.bench import artifact

        base = completed("p1", "python", "consistent", None, "consistent")
        for category in ("invented", [], {}, 1):
            with self.subTest(category=category), self.assertRaisesRegex(ValueError, "category"):
                artifact.validate_benchmark_row({**base, "category": category}, require_result=True)

    def test_hashes_inputs_and_captures_commit_cli_and_settings(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            dataset = repo / "dataset.jsonl"
            skill = repo / "skills" / "evergreen" / "SKILL.md"
            bench = repo / "eval" / "bench"
            judge = bench / "run_bench.py"
            skill.parent.mkdir(parents=True)
            judge.parent.mkdir(parents=True)
            dataset.write_bytes(b'{"id":"one"}\n')
            skill.write_bytes(b"skill body\n")
            modules = {
                "artifact.py": b"artifact body\n",
                "frozen_run.py": b"frozen run body\n",
                "java_context.py": b"java context body\n",
                "metrics.py": b"metrics body\n",
                "model-output.schema.json": b"{}\n",
                "report.py": b"report body\n",
                "resolver.py": b"resolver body\n",
                "run_bench.py": b"judge body\n",
                "run_peer.py": b"peer judge body\n",
                "runner.py": b"runner body\n",
                "split_manifest.py": b"split manifest body\n",
                "trial.py": b"trial body\n",
                "../peers.py": b"peer protocol body\n",
                "../peers-v1.json": b"peer manifest body\n",
                "../peer_adapters/common.py": b"peer adapter common body\n",
                "../peer_adapters/drift_guardian.py": b"peer adapter body\n",
                "../peer_adapters/drift_guardian_runner.js": b"peer adapter runner body\n",
            }
            for name, payload in modules.items():
                path = bench / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            settings = {"models": {"strong": "opus", "cheap": "sonnet"}, "concurrency": 2}
            git = {
                "commit": "abc123", "tree": "tree123", "dirty": True,
                "status_sha256": hashlib.sha256(b" M judge\0").hexdigest(),
                "diff_sha256": hashlib.sha256(b"diff").hexdigest(),
                "untracked_sha256": hashlib.sha256(b"").hexdigest(),
            }
            with mock.patch("eval.bench.artifact._command_output",
                            return_value="2.7.1 (Claude Code)"), \
                 mock.patch("eval.bench.artifact.git_identity", return_value=git):
                metadata = artifact.artifact_metadata(dataset, repo, settings)

        self.assertEqual(metadata["dataset"]["sha256"], hashlib.sha256(b'{"id":"one"}\n').hexdigest())
        self.assertEqual(metadata["skill"]["sha256"], hashlib.sha256(b"skill body\n").hexdigest())
        self.assertEqual(
            [item["path"] for item in metadata["judge"]["files"]],
            [(bench / name).resolve().relative_to(repo.resolve()).as_posix()
             for name in artifact.JUDGE_MODULES],
        )
        self.assertNotEqual(
            metadata["judge"]["sha256"], hashlib.sha256(b"judge body\n").hexdigest()
        )
        self.assertEqual(metadata["git"], git)
        self.assertEqual(metadata["provider"], "claude")
        self.assertEqual(metadata["cli_version"], "2.7.1 (Claude Code)")
        self.assertEqual(metadata["settings"], settings)

    def test_metadata_and_document_serialization_are_deterministic(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            dataset = repo / "dataset.jsonl"
            skill = repo / "skills" / "evergreen" / "SKILL.md"
            bench = repo / "eval" / "bench"
            judge = bench / "run_bench.py"
            skill.parent.mkdir(parents=True)
            judge.parent.mkdir(parents=True)
            dataset.write_text("data")
            skill.write_text("skill")
            for name in artifact.JUDGE_MODULES:
                path = bench / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            git = {"commit": "c", "tree": "t", "dirty": False,
                   "status_sha256": hashlib.sha256(b"").hexdigest(),
                   "diff_sha256": hashlib.sha256(b"").hexdigest(),
                   "untracked_sha256": hashlib.sha256(b"").hexdigest()}
            with mock.patch("eval.bench.artifact._command_output", return_value="fixed"), \
                 mock.patch("eval.bench.artifact.git_identity", return_value=git):
                first = artifact.artifact_metadata(dataset, repo, {"z": 1, "a": {"y": 2, "x": 3}})
                second = artifact.artifact_metadata(dataset, repo, {"a": {"x": 3, "y": 2}, "z": 1})

        rows = [completed("b", "python", "consistent", None, "consistent")]
        one = artifact.artifact_document(
            rows, first, started_at="2026-01-02T03:04:05Z", elapsed_seconds=1.25
        )
        two = artifact.artifact_document(
            rows, second, started_at="2026-01-02T03:04:05Z", elapsed_seconds=1.25
        )
        self.assertEqual(artifact.dumps(one), artifact.dumps(two))
        self.assertNotIn("provider_usage", one)
        with_usage = artifact.artifact_document(
            rows, first, started_at="2026-01-02T03:04:05Z", elapsed_seconds=1.25,
            provider_usage={"output_tokens": 7, "input_tokens": 11},
        )
        self.assertEqual(with_usage["provider_usage"], {"input_tokens": 11, "output_tokens": 7})
        self.assertEqual(with_usage["timing"]["elapsed_seconds"], 1.25)

    def test_cli_provenance_uses_the_declared_provider(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            dataset = repo / "dataset.jsonl"
            skill = repo / "skills" / "evergreen" / "SKILL.md"
            bench = repo / "eval" / "bench"
            skill.parent.mkdir(parents=True)
            bench.mkdir(parents=True)
            dataset.write_text("data")
            skill.write_text("skill")
            for name in artifact.JUDGE_MODULES:
                path = bench / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            commands = []

            def version(command):
                commands.append(command)
                return "codex-cli 0.144.1"

            with mock.patch("eval.bench.artifact._command_output", side_effect=version), \
                 mock.patch("eval.bench.artifact.git_identity", return_value={"commit": "c"}):
                metadata = artifact.artifact_metadata(
                    dataset, repo,
                    {"provider": "codex", "models": {"strong": "gpt", "cheap": "gpt"}},
                )

        self.assertEqual(commands, [["codex", "--version"]])
        self.assertEqual(metadata["provider"], "codex")
        self.assertEqual(metadata["cli_version"], "codex-cli 0.144.1")
        self.assertEqual(metadata["settings"]["provider"], "codex")

    def test_judge_identity_changes_with_every_behavior_module(self):
        from eval.bench import artifact

        expected = {
            "artifact.py", "frozen_run.py", "java_context.py", "metrics.py",
            "model-output.schema.json", "report.py", "resolver.py", "run_bench.py",
            "run_peer.py", "runner.py", "split_manifest.py", "trial.py", "../peers.py",
            "../peers-v1.json", "../peer_adapters/common.py",
            "../peer_adapters/drift_guardian.py",
            "../peer_adapters/drift_guardian_runner.js",
        }
        self.assertEqual(set(artifact.JUDGE_MODULES), expected)
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            bench = repo / "eval" / "bench"
            bench.mkdir(parents=True)
            for name in expected:
                path = bench / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(name)
            first = artifact.judge_identity(repo)
            for name in expected:
                with self.subTest(name=name):
                    path = bench / name
                    original = path.read_text()
                    path.write_text(original + " changed")
                    self.assertNotEqual(artifact.judge_identity(repo)["sha256"], first["sha256"])
                    path.write_text(original)

    def test_hashing_streams_in_bounded_chunks(self):
        from eval.bench import artifact

        reads = []
        real_read = os.read

        def recording_read(descriptor, size):
            reads.append(size)
            return real_read(descriptor, size)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b"abcdef")
            with mock.patch.object(artifact, "HASH_CHUNK_BYTES", 2), \
                 mock.patch.object(artifact.os, "read", side_effect=recording_read):
                digest = artifact.sha256_file(path)

        self.assertEqual(digest, hashlib.sha256(b"abcdef").hexdigest())
        self.assertTrue(reads)
        self.assertNotIn(-1, reads)
        self.assertLessEqual(max(reads), 2)

    def test_reads_and_hashes_reject_symlinks_and_special_files(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.write_bytes(b"payload")
            link = root / "link"
            link.symlink_to(target)
            for operation in (
                lambda path: artifact.read_bytes(path, 100, label="dataset"),
                lambda path: artifact.sha256_file(path, 100),
            ):
                with self.subTest(operation=operation, kind="symlink"), \
                     self.assertRaisesRegex(ValueError, "regular file"):
                    operation(link)
                if Path("/dev/null").exists():
                    with self.subTest(operation=operation, kind="device"), \
                         self.assertRaisesRegex(ValueError, "regular file"):
                        operation(Path("/dev/null"))

    def test_post_open_identity_check_rejects_same_inode_symlink_swap(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "input"
            moved = root / "moved"
            path.write_bytes(b"payload")
            real_lstat = os.lstat
            calls = 0

            def swapping_lstat(candidate):
                nonlocal calls
                calls += 1
                if calls == 1:
                    before = real_lstat(candidate)
                    path.rename(moved)
                    path.symlink_to(moved)
                    return before
                return real_lstat(candidate)

            with mock.patch.object(artifact.os, "lstat", side_effect=swapping_lstat), \
                 mock.patch.object(artifact.os, "O_NOFOLLOW", 0), \
                 self.assertRaisesRegex(ValueError, "regular file"):
                artifact.read_bytes(path, 100, label="dataset")

    def test_read_refuses_when_nonblocking_open_is_unavailable(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b"payload")
            with mock.patch.object(artifact.os, "O_NONBLOCK", None), \
                 self.assertRaisesRegex(ValueError, "nonblocking"):
                artifact.read_bytes(path, 100, label="dataset")

    def test_deadline_contract_names_uninterruptible_filesystem_calls(self):
        from eval.bench import artifact

        self.assertIn("between filesystem calls", artifact.read_bytes.__doc__)
        self.assertIn("cannot preempt", artifact.read_bytes.__doc__)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is unavailable")
    def test_fifo_rejection_never_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "input.fifo"
            os.mkfifo(fifo)
            script = """
from pathlib import Path
import sys
from eval.bench import artifact
path = Path(sys.argv[2])
if sys.argv[1] == 'read':
    artifact.read_bytes(path, 100, timeout=0.1, label='dataset')
else:
    artifact.sha256_file(path, 100, deadline=0)
"""
            for operation in ("read", "hash"):
                with self.subTest(operation=operation):
                    completed = subprocess.run(
                        [sys.executable, "-c", script, operation, str(fifo)],
                        cwd=Path(__file__).parent.parent,
                        capture_output=True,
                        text=True,
                        timeout=1,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn("regular file", completed.stderr)

    def test_bounded_read_enforces_byte_and_time_limits(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "input"
            path.write_bytes(b"12345")
            with self.assertRaisesRegex(ValueError, "dataset too large"):
                artifact.read_bytes(path, 4, timeout=10, label="dataset")
            with self.assertRaisesRegex(ValueError, "dataset read exceeded"):
                artifact.read_bytes(path, 10, timeout=-1, label="dataset")

    def test_command_capture_has_a_wall_clock_deadline(self):
        from eval.bench import artifact

        with self.assertRaises(subprocess.TimeoutExpired):
            artifact._process_bytes(
                [sys.executable, "-c", "import time; time.sleep(1)"], 100, timeout=0.01
            )

    def test_git_identity_changes_for_dirty_status_diff_and_records_tree(self):
        from eval.bench import artifact

        outputs = {
            ("rev-parse", "HEAD"): b"commit\n",
            ("rev-parse", "HEAD^{tree}"): b"tree\n",
            ("status", "--porcelain=v1", "-z", "--untracked-files=all"): b" M file\0",
            ("diff", "--no-ext-diff", "--binary", "HEAD", "--"): b"patch",
            ("ls-files", "--others", "--exclude-standard", "-z"): b"",
        }
        with mock.patch("eval.bench.artifact._git_bytes",
                        side_effect=lambda _repo, *args: outputs[args]):
            identity = artifact.git_identity(Path("/repo"))

        self.assertTrue(identity["dirty"])
        self.assertEqual(identity["commit"], "commit")
        self.assertEqual(identity["tree"], "tree")
        self.assertEqual(identity["status_sha256"], hashlib.sha256(b" M file\0").hexdigest())
        self.assertEqual(identity["diff_sha256"], hashlib.sha256(b"patch").hexdigest())
        self.assertEqual(identity["untracked_sha256"], hashlib.sha256(b"").hexdigest())

    def test_git_identity_hashes_untracked_file_contents(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "new.txt").write_text("first")
            fixed = {
                ("rev-parse", "HEAD"): b"c\n",
                ("rev-parse", "HEAD^{tree}"): b"t\n",
                ("status", "--porcelain=v1", "-z", "--untracked-files=all"): b"?? new.txt\0",
                ("diff", "--no-ext-diff", "--binary", "HEAD", "--"): b"",
                ("ls-files", "--others", "--exclude-standard", "-z"): b"new.txt\0",
            }
            with mock.patch("eval.bench.artifact._git_bytes",
                            side_effect=lambda _repo, *args: fixed[args]):
                first = artifact.git_identity(repo)
                (repo / "new.txt").write_text("second")
                second = artifact.git_identity(repo)

        self.assertNotEqual(first["untracked_sha256"], second["untracked_sha256"])

    def test_untracked_hash_enforces_total_byte_and_time_limits(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / "large.bin").write_bytes(b"12345")
            with self.assertRaisesRegex(ValueError, "untracked files exceed"):
                artifact._untracked_hash(repo, b"large.bin\0", max_bytes=4, timeout=10)
            with self.assertRaisesRegex(ValueError, "untracked hashing exceeded"):
                artifact._untracked_hash(repo, b"large.bin\0", max_bytes=10, timeout=-1)

    def test_resume_requires_exact_provenance_and_accumulates_accounting(self):
        from eval.bench import artifact

        metadata = {"dataset": {"sha256": "a"}, "git": {"commit": "c"}}
        existing = artifact.artifact_document(
            [completed("p1", "python", "consistent", None, "consistent")], metadata,
            started_at="2026-01-01T00:00:00Z", elapsed_seconds=4.5,
            provider_usage={"input_tokens": 10, "nested": {"requests": 1}},
        )
        state = artifact.resume_state(existing, metadata)
        self.assertEqual(state["started_at"], "2026-01-01T00:00:00Z")
        self.assertEqual(state["elapsed_seconds"], 4.5)
        self.assertEqual(state["provider_usage"]["input_tokens"], 10)
        self.assertEqual(
            artifact.merge_usage(state["provider_usage"],
                                 {"input_tokens": 3, "nested": {"requests": 2}}),
            {"input_tokens": 13, "nested": {"requests": 3}},
        )
        with self.assertRaisesRegex(ValueError, "provenance"):
            artifact.resume_state(existing, {"dataset": {"sha256": "different"}})
        with self.assertRaisesRegex(ValueError, "legacy"):
            artifact.resume_state(existing["rows"], metadata)
        existing["timing"]["elapsed_seconds"] = True
        with self.assertRaisesRegex(ValueError, "timing"):
            artifact.resume_state(existing, metadata)
        existing["timing"]["elapsed_seconds"] = 1
        existing["provider_usage"] = {"input_tokens": "many"}
        with self.assertRaisesRegex(ValueError, "numeric"):
            artifact.resume_state(existing, metadata)

    def test_resume_rows_must_exactly_match_hashed_dataset(self):
        from eval.bench import artifact

        dataset = [{
            "id": "p1", "func": "f", "code": "return 1", "doc": "returns 1",
            "language": "python", "label": "consistent", "category": None,
        }]
        metadata = {"dataset": {"sha256": "a"}}
        document = artifact.artifact_document(
            [{**dataset[0], "code": "tampered", "got": {
                "final_status": "complete", "final_verdict": "consistent"
            }}], metadata, started_at="2026-01-01T00:00:00Z", elapsed_seconds=1,
        )
        with self.assertRaisesRegex(ValueError, "dataset"):
            artifact.resume_state(document, metadata, dataset_rows=dataset)

    def test_input_hash_revalidation_detects_mid_run_mutation(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "dataset.jsonl"
            skill = Path(directory) / "SKILL.md"
            dataset.write_text("original")
            skill.write_text("skill")
            metadata = {
                "dataset": {"sha256": artifact.sha256_file(dataset)},
                "skill": {"sha256": artifact.sha256_file(skill)},
            }
            artifact.validate_input_hashes(metadata, dataset, skill)
            dataset.write_text("mutated")
            with self.assertRaisesRegex(ValueError, "dataset changed"):
                artifact.validate_input_hashes(metadata, dataset, skill)
            dataset.write_bytes(b"x" * 10)
            with self.assertRaisesRegex(ValueError, "dataset changed"):
                artifact.validate_input_hashes(
                    metadata, dataset, skill, dataset_max_bytes=4, skill_max_bytes=10
                )

    def test_atomic_write_preserves_previous_artifact_if_replace_fails(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text('{"old":true}')
            with mock.patch("eval.bench.artifact.os.replace", side_effect=OSError("interrupted")):
                with self.assertRaisesRegex(OSError, "interrupted"):
                    artifact.atomic_write_json(path, {"new": True})
            self.assertEqual(json.loads(path.read_text()), {"old": True})
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_oversized_streamed_json_preserves_previous_artifact(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "artifact.json"
            path.write_text('{"old":true}')
            with mock.patch("eval.bench.artifact.json.dumps", side_effect=AssertionError("eager")):
                with self.assertRaisesRegex(ValueError, "generated artifact exceeds"):
                    artifact.atomic_write_json(path, {"payload": "x" * 100}, max_bytes=32)
            self.assertEqual(json.loads(path.read_text()), {"old": True})
            self.assertEqual(list(Path(directory).iterdir()), [path])


class ExecutionLedgerValidationTests(unittest.TestCase):
    def test_valid_clear_ledger_passes_validation(self):
        from eval.bench import artifact

        row = completed_v3(
            "p1", "python", "consistent", None, "consistent", None, clear_execution(attempts=2)
        )
        artifact.validate_benchmark_row(row, require_result=True)  # must not raise

    def test_valid_jury_ledger_passes_validation(self):
        from eval.bench import artifact

        row = completed_v3(
            "p2", "python", "inconsistent", "direct-mismatch", "inconsistent",
            "direct-mismatch", jury_execution(),
        )
        artifact.validate_benchmark_row(row, require_result=True)

    def test_historical_rows_without_execution_remain_valid(self):
        from eval.bench import artifact

        row = completed("p3", "python", "consistent", None, "consistent")
        artifact.validate_benchmark_row(row, require_result=True)

    def test_negative_counts_are_rejected(self):
        from eval.bench import artifact

        bad = clear_execution(attempts=1)
        bad["provider_attempts"] = -1
        row = completed_v3("p4", "python", "consistent", None, "consistent", None, bad)
        with self.assertRaisesRegex(ValueError, "provider_attempts"):
            artifact.validate_benchmark_row(row, require_result=True)

    def test_mismatched_sums_are_rejected(self):
        from eval.bench import artifact

        bad = jury_execution()
        bad["attempts_by_tier"]["cheap"] += 1
        row = completed_v3(
            "p5", "python", "inconsistent", "direct-mismatch", "inconsistent",
            "direct-mismatch", bad,
        )
        with self.assertRaisesRegex(ValueError, "do not sum"):
            artifact.validate_benchmark_row(row, require_result=True)

    def test_unknown_stage_names_are_rejected(self):
        from eval.bench import artifact

        bad = jury_execution()
        bad["attempts_by_stage"]["mystery"] = 1
        bad["provider_attempts"] += 1
        bad["attempts_by_tier"]["cheap"] += 1
        row = completed_v3(
            "p6", "python", "inconsistent", "direct-mismatch", "inconsistent",
            "direct-mismatch", bad,
        )
        with self.assertRaisesRegex(ValueError, "attempts_by_stage"):
            artifact.validate_benchmark_row(row, require_result=True)

    def test_unknown_or_missing_tier_keys_are_rejected(self):
        from eval.bench import artifact

        for mutate in (
            lambda execution: execution["attempts_by_tier"].pop("strong"),
            lambda execution: execution["attempts_by_tier"].__setitem__("gpu", 0),
        ):
            with self.subTest(mutate=mutate):
                bad = jury_execution()
                mutate(bad)
                row = completed_v3(
                    "p7", "python", "inconsistent", "direct-mismatch", "inconsistent",
                    "direct-mismatch", bad,
                )
                with self.assertRaisesRegex(ValueError, "attempts_by_tier"):
                    artifact.validate_benchmark_row(row, require_result=True)

    def test_unknown_route_value_is_rejected(self):
        from eval.bench import artifact

        bad = clear_execution()
        bad["route"] = "maybe"
        row = completed_v3("p8", "python", "consistent", None, "consistent", None, bad)
        with self.assertRaisesRegex(ValueError, "route is invalid"):
            artifact.validate_benchmark_row(row, require_result=True)

    def test_unknown_strategy_is_rejected(self):
        from eval.bench import artifact

        bad = clear_execution()
        bad["strategy"] = "cascade-v2"
        row = completed_v3("p9", "python", "consistent", None, "consistent", None, bad)
        with self.assertRaisesRegex(ValueError, "strategy"):
            artifact.validate_benchmark_row(row, require_result=True)

    def test_route_decision_inconsistency_is_rejected(self):
        from eval.bench import artifact

        # A clear route claiming a verdict other than consistent.
        wrong_verdict = completed_v3(
            "p10", "python", "consistent", None, "inconsistent", "direct-mismatch",
            clear_execution(),
        )
        with self.assertRaisesRegex(ValueError, "does not match its decision"):
            artifact.validate_benchmark_row(wrong_verdict, require_result=True)

        # A clear route carrying a non-screen stage — inconsistent with a one-call clear.
        extra_stage = clear_execution()
        extra_stage["attempts_by_stage"]["snap"] = 1
        extra_stage["provider_attempts"] += 1
        extra_stage["attempts_by_tier"]["strong"] += 1
        wrong_stage = completed_v3(
            "p11", "python", "consistent", None, "consistent", None, extra_stage,
        )
        with self.assertRaisesRegex(ValueError, "inconsistent with its route"):
            artifact.validate_benchmark_row(wrong_stage, require_result=True)

        # A jury route missing its own snap stage — every jury run calls snap first.
        no_snap = jury_execution()
        del no_snap["attempts_by_stage"]["snap"]
        no_snap["provider_attempts"] -= 1
        no_snap["attempts_by_tier"]["strong"] -= 1
        missing_snap = completed_v3(
            "p12", "python", "inconsistent", "direct-mismatch", "inconsistent",
            "direct-mismatch", no_snap,
        )
        with self.assertRaisesRegex(ValueError, "missing the jury's own snap"):
            artifact.validate_benchmark_row(missing_snap, require_result=True)


class ResumeProviderAttemptBudgetTests(unittest.TestCase):
    def test_resume_sums_prior_provider_attempts_from_v3_rows_only(self):
        from eval.bench import artifact

        metadata = {"dataset": {"sha256": "a"}, "git": {"commit": "c"}}
        rows = [
            completed("p1", "python", "consistent", None, "consistent"),  # v1/v2: no ledger
            completed_v3(
                "p2", "python", "consistent", None, "consistent", None,
                clear_execution(attempts=3),
            ),
            completed_v3(
                "p3", "python", "inconsistent", "direct-mismatch", "inconsistent",
                "direct-mismatch", jury_execution(),  # totals 7
            ),
        ]
        document = artifact.artifact_document(
            rows, metadata, started_at="2026-01-01T00:00:00Z", elapsed_seconds=1,
        )

        state = artifact.resume_state(document, metadata)

        self.assertEqual(state["prior_provider_attempts"], 3 + 7)

    def test_resume_ignores_top_level_provider_usage_for_attempt_counts(self):
        from eval.bench import artifact

        metadata = {"dataset": {"sha256": "a"}, "git": {"commit": "c"}}
        rows = [completed_v3(
            "p1", "python", "consistent", None, "consistent", None, clear_execution(attempts=2)
        )]
        document = artifact.artifact_document(
            rows, metadata, started_at="2026-01-01T00:00:00Z", elapsed_seconds=1,
            provider_usage={"input_tokens": 999999},
        )

        state = artifact.resume_state(document, metadata)

        # provider_usage is an unrelated token-accounting field; call counts never come from it.
        self.assertEqual(state["prior_provider_attempts"], 2)

    def test_resume_without_a_ceiling_never_raises_but_still_reports_prior_attempts(self):
        from eval.bench import artifact

        metadata = {"dataset": {"sha256": "a"}, "git": {"commit": "c"}}
        rows = [completed_v3(
            "p1", "python", "consistent", None, "consistent", None,
            clear_execution(attempts=1000),
        )]
        document = artifact.artifact_document(
            rows, metadata, started_at="2026-01-01T00:00:00Z", elapsed_seconds=1,
        )

        state = artifact.resume_state(document, metadata)

        self.assertEqual(state["prior_provider_attempts"], 1000)

    def test_resume_rejects_an_artifact_whose_stored_attempts_exceed_the_ceiling(self):
        from eval.bench import artifact

        metadata = {"dataset": {"sha256": "a"}, "git": {"commit": "c"}}
        rows = [completed_v3(
            "p1", "python", "consistent", None, "consistent", None,
            clear_execution(attempts=10),
        )]
        document = artifact.artifact_document(
            rows, metadata, started_at="2026-01-01T00:00:00Z", elapsed_seconds=1,
        )

        with self.assertRaisesRegex(ValueError, "exceed"):
            artifact.resume_state(document, metadata, provider_attempt_ceiling=9)
        # At exactly the ceiling, resume is still allowed — remaining is simply zero.
        self.assertEqual(
            artifact.resume_state(
                document, metadata, provider_attempt_ceiling=10
            )["prior_provider_attempts"],
            10,
        )

    def test_runner_nets_the_remaining_budget_from_ceiling_minus_prior_attempts(self):
        from eval.bench import artifact

        metadata = {"dataset": {"sha256": "a"}, "git": {"commit": "c"}}
        rows = [completed_v3(
            "p1", "python", "consistent", None, "consistent", None,
            clear_execution(attempts=4),
        )]
        document = artifact.artifact_document(
            rows, metadata, started_at="2026-01-01T00:00:00Z", elapsed_seconds=1,
        )

        state = artifact.resume_state(document, metadata, provider_attempt_ceiling=10)
        budget = runner.build_provider_attempt_budget(10, state["prior_provider_attempts"])

        # Resume can only spend the shared budget down, never reset it.
        self.assertEqual(budget.remaining, 6)

    def test_resume_rejects_a_changed_provider_attempt_ceiling_as_incompatible_provenance(self):
        from eval.bench import artifact

        # The ceiling is frozen into metadata via settings["max_provider_attempts"] (runner.py's
        # eval_settings / frozen_run.py's run_policy). resume_state's generic metadata-equality
        # check is what turns a changed ceiling into a rejected resume: the launch-time metadata
        # (bound to the NEW ceiling) will never equal a live artifact's stored metadata (bound
        # to the OLD one), regardless of what provider_attempt_ceiling itself is passed here.
        metadata = {
            "dataset": {"sha256": "a"}, "git": {"commit": "c"},
            "settings": {"max_provider_attempts": 50},
        }
        rows = [completed_v3(
            "p1", "python", "consistent", None, "consistent", None,
            clear_execution(attempts=3),
        )]
        document = artifact.artifact_document(
            rows, metadata, started_at="2026-01-01T00:00:00Z", elapsed_seconds=1,
        )
        changed_ceiling_metadata = {
            **metadata, "settings": {"max_provider_attempts": 100},
        }

        with self.assertRaisesRegex(ValueError, "provenance"):
            artifact.resume_state(
                document, changed_ceiling_metadata, provider_attempt_ceiling=100,
            )
        # The same ceiling resumes cleanly.
        self.assertEqual(
            artifact.resume_state(
                document, metadata, provider_attempt_ceiling=50,
            )["prior_provider_attempts"],
            3,
        )


class ArtifactReportTests(unittest.TestCase):
    def metadata(self, dataset="dataset"):
        digest = hashlib.sha256(dataset.encode()).hexdigest()
        return {
            "dataset": {"path": f"{dataset}.jsonl", "sha256": digest},
            "provider": "claude",
            "skill": {"path": "skills/evergreen/SKILL.md", "sha256": "1" * 64},
            "judge": {"path": "eval/bench/run_bench.py", "sha256": "2" * 64},
            "git": {"commit": "c" * 40, "tree": "d" * 40, "dirty": False,
                    "status_sha256": hashlib.sha256(b"").hexdigest(),
                    "diff_sha256": hashlib.sha256(b"").hexdigest(),
                    "untracked_sha256": hashlib.sha256(b"").hexdigest()},
            "cli_version": "claude 1.0",
            "settings": {"models": {"cheap": "sonnet", "strong": "opus"}},
        }

    def write_artifact(self, directory, name, rows, metadata=None):
        path = Path(directory) / name
        path.write_text(json.dumps({
            "schema_version": 1, "metadata": metadata or self.metadata(name),
            "timing": {"started_at": "2026-01-01T00:00:00Z", "elapsed_seconds": 1},
            "rows": rows,
        }))
        return path

    def test_markdown_is_deterministic_and_never_aggregates_languages(self):
        from eval.bench import report

        with tempfile.TemporaryDirectory() as directory:
            python = self.write_artifact(directory, "z-python.json", [
                completed("p2", "python", "consistent", None, "consistent"),
                completed("p1", "python", "inconsistent", "direct-mismatch", "inconsistent"),
            ])
            go = self.write_artifact(directory, "a-go.json", [
                completed("g2", "go", "consistent", None, "consistent"),
                completed("g1", "go", "inconsistent", "over-promise", "inconsistent"),
            ])
            first = report.render_markdown(
                [python, go], required_languages=["python", "go"], coverage_threshold=1.0
            )
            second = report.render_markdown(
                [go, python], required_languages=["go", "python"], coverage_threshold=1.0
            )

        self.assertEqual(first, second)
        self.assertLess(first.index("## go"), first.index("## python"))
        self.assertEqual(first.count("| Attempted | 2 |"), 2)
        self.assertNotIn("all languages", first.lower())
        self.assertNotIn("aggregate", first.lower())
        self.assertIn("### Provenance", first)
        self.assertIn("| Provider | claude |", first)
        self.assertIn("claude 1.0", first)
        self.assertIn("Required languages: **go, python**.", first)

    def test_cli_writes_report_and_fails_below_coverage_threshold(self):
        from eval.bench import report

        rows = [
            completed("p1", "python", "inconsistent", "direct-mismatch", "inconsistent"),
            {
                "id": "p2", "language": "python", "label": "consistent", "category": None,
                "got": {"final_status": "abstain", "final_verdict": None},
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            artifact_path = self.write_artifact(directory, "artifact.json", rows)
            markdown = Path(directory) / "report.md"
            status = report.main([
                str(artifact_path), "--markdown", str(markdown),
                "--require-language", "python", "--coverage-threshold", "0.75"
            ])
            text = markdown.read_text()

        self.assertEqual(status, 2)
        self.assertIn("Provider coverage: **50.0%**", text)
        self.assertIn("FAIL", text)

    def test_quality_gate_cannot_hide_low_decision_coverage_with_unverified_rows(self):
        from eval.bench import report

        rows = [
            completed("p1", "python", "inconsistent", "direct-mismatch", "inconsistent"),
            completed("p2", "python", "consistent", None, "consistent"),
        ]
        rows.extend({
            "id": f"u{index}", "language": "python", "label": "consistent",
            "category": None, "got": {
                "final_status": "complete", "semantic_status": "unverified",
                "final_verdict": None,
            },
        } for index in range(8))
        with tempfile.TemporaryDirectory() as directory:
            artifact_path = self.write_artifact(directory, "artifact.json", rows)
            markdown = Path(directory) / "report.md"
            status = report.main([
                str(artifact_path), "--markdown", str(markdown),
                "--require-language", "python", "--coverage-threshold", "1.0",
                "--decision-threshold", "0.90", "--precision-threshold", "0.90",
                "--recall-threshold", "0.90", "--f1-threshold", "0.90",
            ])
            text = markdown.read_text()

        self.assertEqual(status, 2)
        self.assertIn("Provider coverage: **100.0%**", text)
        self.assertIn("Decision coverage: **20.0%**", text)
        self.assertIn("| Unverified | 8 |", text)
        # Binary scoring folds the 8 unverified consistent rows into the matrix as TN.
        self.assertIn("| TN | 9 |", text)

    def test_tiny_perfect_sample_fails_minimum_reference_class_counts(self):
        from eval.bench import report

        rows = [
            completed("p1", "python", "inconsistent", "direct-mismatch", "inconsistent"),
            completed("n1", "python", "consistent", None, "consistent"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            artifact_path = self.write_artifact(directory, "artifact.json", rows)
            markdown = Path(directory) / "report.md"
            status = report.main([
                str(artifact_path), "--markdown", str(markdown),
                "--require-language", "python", "--precision-threshold", "1.0",
                "--recall-threshold", "1.0", "--f1-threshold", "1.0",
                "--minimum-reference-positive-decisions", "20",
                "--minimum-reference-negative-decisions", "20",
            ])
            text = markdown.read_text()

        self.assertEqual(status, 2)
        self.assertIn("Reference positive decisions", text)
        self.assertNotIn("Human", text)
        self.assertIn("1 / 20", text)

    def test_result_validation_enforces_semantic_status_combinations(self):
        from eval.bench import artifact

        base = completed("p1", "python", "consistent", None, "consistent")
        invalid_results = [
            {"final_status": "complete", "semantic_status": "decided",
             "final_verdict": None},
            {"final_status": "complete", "semantic_status": "unverified",
             "final_verdict": "consistent"},
            {"final_status": "abstain", "semantic_status": "decided",
             "final_verdict": None},
            {"final_status": "complete", "semantic_status": "unknown",
             "final_verdict": None},
        ]
        for got in invalid_results:
            with self.subTest(got=got), self.assertRaisesRegex(ValueError, "result"):
                artifact.validate_benchmark_row({**base, "got": got}, require_result=True)

    def test_cli_accepts_coverage_at_threshold(self):
        from eval.bench import report

        rows = [
            completed(f"p{index}", "python", "inconsistent", "direct-mismatch",
                      "inconsistent")
            for index in range(20)
        ] + [
            completed(f"n{index}", "python", "consistent", None, "consistent")
            for index in range(20)
        ]
        with tempfile.TemporaryDirectory() as directory:
            artifact_path = self.write_artifact(directory, "artifact.json", rows)
            markdown = Path(directory) / "report.md"
            status = report.main([
                str(artifact_path), "--markdown", str(markdown),
                "--require-language", "python",
            ])

        self.assertEqual(status, 0)

    def test_report_requires_exact_predeclared_languages(self):
        from eval.bench import report

        with tempfile.TemporaryDirectory() as directory:
            python = self.write_artifact(directory, "python.json", [
                completed("p1", "Python", "consistent", None, "consistent")
            ])
            go = self.write_artifact(directory, "go.json", [
                completed("g1", "go", "consistent", None, "consistent")
            ])

            with self.assertRaisesRegex(ValueError, "missing artifacts.*go"):
                report.render_markdown([python], required_languages=["Python", "go"])
            with self.assertRaisesRegex(ValueError, "undeclared artifacts.*go"):
                report.render_markdown([python, go], required_languages=["Python"])

    def test_required_language_declaration_is_explicit_unique_and_bounded(self):
        from eval.bench import report

        rows = [completed("p1", "Python", "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            artifact_path = self.write_artifact(directory, "python.json", rows)
            for declared, phrase in (
                ([], "explicitly declared"),
                (["Python", "Python"], "duplicate"),
                ([""], "non-empty"),
                ([" "], "non-empty"),
                (["x" * 129], "128"),
                ([f"lang-{index}" for index in range(65)], "maximum 64"),
            ):
                with self.subTest(declared=declared), self.assertRaisesRegex(ValueError, phrase):
                    report.render_markdown(
                        [artifact_path], required_languages=declared
                    )

    def test_cli_requires_language_declarations_and_rejects_unknown_ones(self):
        from eval.bench import report

        rows = [completed("p1", "Python", "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            artifact_path = self.write_artifact(directory, "python.json", rows)
            markdown = Path(directory) / "report.md"
            missing_status = report.main([
                str(artifact_path), "--markdown", str(markdown)
            ])
            missing_text = markdown.read_text()
            unknown_status = report.main([
                str(artifact_path), "--markdown", str(markdown),
                "--require-language", "Python", "--require-language", "go",
            ])
            unknown_text = markdown.read_text()

        self.assertEqual(missing_status, 2)
        self.assertIn("explicitly declared", missing_text)
        self.assertEqual(unknown_status, 2)
        self.assertIn("missing artifacts", unknown_text)

    def test_documented_current_report_declares_all_five_languages(self):
        readme = (
            Path(__file__).parents[1] / "eval" / "bench" / "README.md"
        ).read_text()

        self.assertIn("python3 eval/bench/report.py", readme)
        for language in ("Python", "Java", "typescript", "rust", "go"):
            with self.subTest(language=language):
                self.assertIn(f"--require-language {language}", readme)

    def test_report_rejects_incompatible_provenance(self):
        from eval.bench import report

        rows = [completed("p1", "python", "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            first = self.write_artifact(directory, "one.json", rows, self.metadata("one"))
            changed = self.metadata("two")
            changed["judge"]["sha256"] = "9" * 64
            second = self.write_artifact(
                directory, "two.json",
                [completed("p2", "go", "consistent", None, "consistent")], changed,
            )
            markdown = Path(directory) / "report.md"
            status = report.main([
                str(first), str(second), "--markdown", str(markdown),
                "--require-language", "python", "--require-language", "go",
            ])
            text = markdown.read_text()

        self.assertEqual(status, 2)
        self.assertIn("FAIL", text)
        self.assertIn("incompatible provenance", text)

    def test_report_compatibility_includes_additional_metadata_fields(self):
        from eval.bench import report

        with tempfile.TemporaryDirectory() as directory:
            one = self.metadata("one")
            two = self.metadata("two")
            one["provider_region"] = "us-west"
            two["provider_region"] = "us-east"
            first = self.write_artifact(directory, "one.json", [
                completed("p1", "python", "consistent", None, "consistent")
            ], one)
            second = self.write_artifact(directory, "two.json", [
                completed("p2", "go", "consistent", None, "consistent")
            ], two)
            markdown = Path(directory) / "report.md"
            status = report.main([
                str(first), str(second), "--markdown", str(markdown),
                "--require-language", "python", "--require-language", "go",
            ])

        self.assertEqual(status, 2)

    def test_report_rejects_duplicate_ids_across_artifacts(self):
        from eval.bench import report

        with tempfile.TemporaryDirectory() as directory:
            first = self.write_artifact(directory, "one.json", [
                completed("same", "python", "consistent", None, "consistent")
            ], self.metadata("one"))
            second = self.write_artifact(directory, "two.json", [
                completed("same", "go", "consistent", None, "consistent")
            ], self.metadata("two"))
            markdown = Path(directory) / "report.md"
            status = report.main([
                str(first), str(second), "--markdown", str(markdown),
                "--require-language", "python", "--require-language", "go",
            ])
            text = markdown.read_text()

        self.assertEqual(status, 2)
        self.assertIn("duplicate pair id", text)

    def test_legacy_and_unavailable_provenance_are_nonpublishable(self):
        from eval.bench import report

        rows = [completed("p1", "python", "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            legacy = Path(directory) / "legacy.json"
            legacy.write_text(json.dumps(rows))
            markdown = Path(directory) / "legacy.md"
            self.assertEqual(report.main([
                str(legacy), "--markdown", str(markdown), "--require-language", "python",
            ]), 2)
            self.assertIn("legacy", markdown.read_text().lower())

            bad = self.metadata("bad")
            bad["cli_version"] = "unavailable"
            artifact_path = self.write_artifact(directory, "bad.json", rows, bad)
            self.assertEqual(report.main([
                str(artifact_path), "--markdown", str(markdown),
                "--require-language", "python",
            ]), 2)
            self.assertIn("unavailable", markdown.read_text().lower())

            bad_hash = self.metadata("bad-hash")
            bad_hash["skill"]["sha256"] = "not-a-hash"
            artifact_path = self.write_artifact(directory, "bad-hash.json", rows, bad_hash)
            self.assertEqual(report.main([
                str(artifact_path), "--markdown", str(markdown),
                "--require-language", "python",
            ]), 2)
            self.assertIn("invalid", markdown.read_text().lower())

            missing_provider = self.metadata("missing-provider")
            del missing_provider["provider"]
            artifact_path = self.write_artifact(
                directory, "missing-provider.json", rows, missing_provider
            )
            self.assertEqual(report.main([
                str(artifact_path), "--markdown", str(markdown),
                "--require-language", "python",
            ]), 2)
            self.assertIn("required metadata", markdown.read_text().lower())

    def test_report_bounds_artifact_count_size_and_rows(self):
        from eval.bench import report

        rows = [completed("p1", "python", "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_artifact(directory, "one.json", rows)
            with mock.patch.object(report, "MAX_ARTIFACTS", 1):
                with self.assertRaisesRegex(ValueError, "too many artifacts"):
                    report.render_markdown([path, path], required_languages=["python"])
            with mock.patch.object(report, "MAX_ARTIFACT_BYTES", 10):
                with self.assertRaisesRegex(ValueError, "too large"):
                    report.render_markdown([path], required_languages=["python"])
            with mock.patch.object(report, "MAX_ROWS", 0):
                with self.assertRaisesRegex(ValueError, "too many rows"):
                    report.render_markdown([path], required_languages=["python"])

    def test_report_rejects_cumulative_bytes_before_loading_any_artifact(self):
        from eval.bench import report

        rows = [completed("p1", "python", "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            first = self.write_artifact(directory, "one.json", rows)
            second = self.write_artifact(directory, "two.json", [
                completed("p2", "go", "consistent", None, "consistent")
            ])
            budget = first.stat().st_size + second.stat().st_size - 1
            with mock.patch.object(report, "MAX_TOTAL_ARTIFACT_BYTES", budget), \
                 mock.patch.object(report, "load_json") as loader:
                with self.assertRaisesRegex(ValueError, "total artifact bytes"):
                    report.render_markdown(
                        [first, second], required_languages=["python", "go"]
                    )
            loader.assert_not_called()

    def test_deep_json_and_provider_metadata_fail_publication_deterministically(self):
        from eval.bench import report

        with tempfile.TemporaryDirectory() as directory:
            deep = Path(directory) / "deep.json"
            deep.write_text("{}")
            markdown = Path(directory) / "report.md"
            with mock.patch.object(report, "load_json", side_effect=RecursionError):
                self.assertEqual(report.main([
                    str(deep), "--markdown", str(markdown),
                    "--require-language", "python",
                ]), 2)
            self.assertIn("artifact nesting exceeds safe limit", markdown.read_text())

            holder = leaf = {}
            for _ in range(2000):
                child = {}
                leaf["nested"] = child
                leaf = child
            shallow = self.metadata("provider")
            document = {
                "schema_version": 1,
                "metadata": shallow,
                "timing": {"started_at": "2026-01-01T00:00:00Z", "elapsed_seconds": 1},
                "provider_usage": holder,
                "rows": [completed("p1", "python", "consistent", None, "consistent")],
            }
            placeholder = Path(directory) / "provider.json"
            placeholder.write_text("{}")
            with mock.patch.object(report, "load_json", return_value=document):
                self.assertEqual(report.main([
                    str(placeholder), "--markdown", str(markdown),
                    "--require-language", "python",
                ]), 2)
            self.assertIn("artifact nesting exceeds safe limit", markdown.read_text())

    def test_language_heading_is_markdown_safe(self):
        from eval.bench import report

        malicious = "python\n# injected <script>"
        rows = [completed("p1", malicious, "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_artifact(directory, "one.json", rows)
            text = report.render_markdown([path], required_languages=[malicious])

        self.assertNotIn("\n# injected", text)
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)

    def test_missing_timing_and_invalid_language_are_nonpublishable(self):
        from eval.bench import report

        with tempfile.TemporaryDirectory() as directory:
            path = self.write_artifact(directory, "one.json", [
                completed("p1", "python", "consistent", None, "consistent")
            ])
            document = json.loads(path.read_text())
            document.pop("timing")
            path.write_text(json.dumps(document))
            markdown = Path(directory) / "report.md"
            self.assertEqual(report.main([
                str(path), "--markdown", str(markdown), "--require-language", "python",
            ]), 2)
            self.assertIn("timing", markdown.read_text().lower())

            document["timing"] = {"started_at": "2026-01-01T00:00:00Z", "elapsed_seconds": 1}
            document["rows"][0]["language"] = ["python"]
            path.write_text(json.dumps(document))
            self.assertEqual(report.main([
                str(path), "--markdown", str(markdown), "--require-language", "python",
            ]), 2)
            self.assertIn("language", markdown.read_text().lower())

    def test_metadata_types_iso_time_and_finite_elapsed_are_strict(self):
        from eval.bench import report

        rows = [completed("p1", "python", "consistent", None, "consistent")]
        cases = []
        bad_git = self.metadata("git")
        bad_git["git"]["dirty"] = 1
        cases.append((bad_git, {"started_at": "2026-01-01T00:00:00Z", "elapsed_seconds": 1}))
        cases.append((self.metadata("empty-cli"), {"started_at": "", "elapsed_seconds": 1}))
        cases.append((self.metadata("nan"), {"started_at": "2026-01-01", "elapsed_seconds": float("nan")}))
        with tempfile.TemporaryDirectory() as directory:
            markdown = Path(directory) / "report.md"
            for index, (metadata, timing) in enumerate(cases):
                with self.subTest(index=index):
                    path = self.write_artifact(directory, f"{index}.json", rows, metadata)
                    document = json.loads(path.read_text())
                    document["timing"] = timing
                    path.write_text(json.dumps(document))
                    self.assertEqual(report.main([
                        str(path), "--markdown", str(markdown),
                        "--require-language", "python",
                    ]), 2)
            path = self.write_artifact(directory, "usage.json", rows, self.metadata("usage"))
            document = json.loads(path.read_text())
            document["provider_usage"] = {"input_tokens": "many"}
            path.write_text(json.dumps(document))
            self.assertEqual(report.main([
                str(path), "--markdown", str(markdown), "--require-language", "python",
            ]), 2)
            path = self.write_artifact(directory, "settings.json", rows, self.metadata("settings"))
            document = json.loads(path.read_text())
            document["metadata"]["settings"] = {"temperature": float("nan")}
            path.write_text(json.dumps(document))
            self.assertEqual(report.main([
                str(path), "--markdown", str(markdown), "--require-language", "python",
            ]), 2)


class ExecutionSummaryReportTests(unittest.TestCase):
    def test_accounting_reports_unverified_for_rows_without_a_ledger(self):
        from eval.bench import report

        rows = [completed("p1", "python", "consistent", None, "consistent")]
        accounting = report.execution_accounting(rows)

        self.assertEqual(accounting["rows"], 1)
        for field in ("clear", "jury", "escalation_rate", "logical_calls",
                      "provider_attempts", "retries", "attempts_per_row"):
            with self.subTest(field=field):
                self.assertIsNone(accounting[field])

    def test_accounting_reports_unverified_for_a_mixed_row_set(self):
        # A mixed row set (some rows carry a valid execution ledger, some don't) must NOT be
        # summed as if only the ledger-bearing rows existed — that silently undercounts
        # provider_attempts and is exactly what let the cascade gate manufacture a PASS. Per
        # the module docstring, "a mixed or all-historical row set reports every derived field
        # as None"; this pins the mixed half of that contract.
        from eval.bench import report

        rows = [
            completed_v3(
                "p1", "python", "consistent", None, "consistent", None,
                clear_execution(attempts=2),
            ),
            completed("p2", "python", "inconsistent", None, "inconsistent"),
        ]

        accounting = report.execution_accounting(rows)

        self.assertEqual(accounting["rows"], 2)
        for field in ("clear", "jury", "escalation_rate", "logical_calls",
                      "provider_attempts", "retries", "attempts_per_row"):
            with self.subTest(field=field):
                self.assertIsNone(accounting[field])

    def test_accounting_sums_ledgers_and_derives_retries_from_the_ledger_not_stage_count(self):
        from eval.bench import report

        rows = [
            completed_v3(
                "p1", "python", "consistent", None, "consistent", None,
                clear_execution(attempts=2),
            ),
            completed_v3(
                "p2", "python", "inconsistent", "direct-mismatch", "inconsistent",
                "direct-mismatch", jury_execution(),  # 7 attempts, 5 logical_calls
            ),
        ]

        accounting = report.execution_accounting(rows)

        self.assertEqual(accounting["rows"], 2)
        self.assertEqual(accounting["clear"], 1)
        self.assertEqual(accounting["jury"], 1)
        self.assertEqual(accounting["escalation_rate"], 0.5)
        self.assertEqual(accounting["logical_calls"], 1 + 5)
        self.assertEqual(accounting["provider_attempts"], 2 + 7)
        # Retries must come straight from provider_attempts - logical_calls on the ledger —
        # never from counting entries under got["stages"], which a retried stage cannot be
        # told apart from a first-try stage in.
        self.assertEqual(accounting["retries"], (2 + 7) - (1 + 5))
        self.assertEqual(accounting["attempts_per_row"], (2 + 7) / 2)

    def test_render_execution_summary_formats_rows_calls_and_budget(self):
        from eval.bench import report

        rows = [
            completed_v3(
                "p1", "python", "consistent", None, "consistent", None,
                clear_execution(attempts=2),
            ),
            completed_v3(
                "p2", "python", "inconsistent", "direct-mismatch", "inconsistent",
                "direct-mismatch", jury_execution(),
            ),
        ]

        summary = report.render_execution_summary(rows, budget=50)

        self.assertEqual(summary, "\n".join([
            "rows=2 clear=1 jury=1 escalation_rate=50.0%",
            "logical_calls=6 provider_attempts=9 retries=3 attempts_per_row=4.50",
            "budget=50 used=9 remaining=41",
        ]))

    def test_render_execution_summary_is_unverified_without_any_ledger(self):
        from eval.bench import report

        rows = [completed("p1", "python", "consistent", None, "consistent")]

        summary = report.render_execution_summary(rows)

        self.assertEqual(summary, "\n".join([
            "rows=1 clear=unverified jury=unverified escalation_rate=unverified",
            "logical_calls=unverified provider_attempts=unverified retries=unverified "
            "attempts_per_row=unverified",
            "budget=unverified used=unverified remaining=unverified",
        ]))

    def test_cli_prints_the_execution_summary_pulling_budget_from_settings(self):
        from eval.bench import report

        rows = [completed_v3(
            "p1", "python", "consistent", None, "consistent", None,
            clear_execution(attempts=4),
        )]
        metadata = {
            "dataset": {"path": "d.jsonl", "sha256": hashlib.sha256(b"d").hexdigest()},
            "provider": "claude",
            "skill": {"path": "skills/evergreen/SKILL.md", "sha256": "1" * 64},
            "judge": {"path": "eval/bench/run_bench.py", "sha256": "2" * 64},
            "git": {"commit": "c" * 40, "tree": "d" * 40, "dirty": False,
                    "status_sha256": hashlib.sha256(b"").hexdigest(),
                    "diff_sha256": hashlib.sha256(b"").hexdigest(),
                    "untracked_sha256": hashlib.sha256(b"").hexdigest()},
            "cli_version": "claude 1.0",
            "settings": {"resolver": "v3", "max_provider_attempts": 20},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v3.json"
            path.write_text(json.dumps({
                "schema_version": 1, "metadata": metadata,
                "timing": {"started_at": "2026-01-01T00:00:00Z", "elapsed_seconds": 1},
                "rows": rows,
            }))
            markdown = Path(directory) / "report.md"
            with mock.patch("builtins.print") as printed:
                status = report.main([
                    str(path), "--markdown", str(markdown), "--require-language", "python",
                ])

        # A single row can never clear the (floor-20) minimum reference decision gate, so the
        # publication verdict itself is FAIL — the execution summary prints regardless, since
        # it is diagnostic accounting, not a publication gate.
        self.assertEqual(status, 2)
        printed.assert_called_once_with(
            "rows=1 clear=1 jury=0 escalation_rate=0.0%\n"
            "logical_calls=1 provider_attempts=4 retries=3 attempts_per_row=4.00\n"
            "budget=20 used=4 remaining=16"
        )


class RunBenchArtifactIntegrationTests(unittest.TestCase):
    def test_checkpoint_write_requires_full_runtime_provenance_to_remain_frozen(self):
        expected = {"git": {"commit": "a"}, "judge": {"sha256": "b"}}
        self.assertIsNone(runner.validate_runtime_metadata(
            expected, Path("dataset"), Path("repo"), {"provider": "codex"},
            metadata_fn=lambda *_args: expected,
        ))
        with self.assertRaisesRegex(ValueError, "provenance changed"):
            runner.validate_runtime_metadata(
                expected, Path("dataset"), Path("repo"), {"provider": "codex"},
                metadata_fn=lambda *_args: {
                    "git": {"commit": "a"}, "judge": {"sha256": "changed"}
                },
            )

    def test_every_atomic_checkpoint_is_synchronously_mirrored(self):
        metadata = {"git": {"commit": "a" * 40, "dirty": False}}
        path = Path("artifact.json")
        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory).resolve()
            status = os.lstat(archive_path)
            token = f"{status.st_dev}:{status.st_ino}:{status.st_mode}"
            with mock.patch("eval.bench.frozen_run.archive_checkpoint") as archive:
                runner.mirror_frozen_checkpoint(
                    path, metadata,
                    {"EVAL_FROZEN_ARCHIVE_DIR": str(archive_path),
                     "EVAL_FROZEN_ARCHIVE_TOKEN": token},
                )
            archive.assert_called_once_with(path, archive_path, metadata)
        with self.assertRaisesRegex(ValueError, "archive"):
            runner.mirror_frozen_checkpoint(path, metadata, {})
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "replaced"):
                runner.mirror_frozen_checkpoint(
                    path, metadata,
                    {"EVAL_FROZEN_ARCHIVE_DIR": str(Path(directory).resolve()),
                     "EVAL_FROZEN_ARCHIVE_TOKEN": "1:2:3"},
                )

    def test_artifact_rows_supports_new_envelope_and_legacy_transcript(self):
        rows = [completed("p1", "python", "consistent", None, "consistent")]
        self.assertEqual(runner.artifact_rows({"schema_version": 1, "rows": rows}), rows)
        self.assertEqual(runner.artifact_rows(rows), rows)

    def test_concurrency_is_strictly_bounded(self):
        for value in ("0", "33", "not-an-int"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "EVAL_CONCURRENCY"):
                runner.eval_concurrency({"EVAL_CONCURRENCY": value})
        self.assertEqual(runner.eval_concurrency({"EVAL_CONCURRENCY": "32"}), 32)

    def test_resolver_accepts_v3(self):
        self.assertEqual(runner.eval_resolver({"EVAL_RESOLVER": "v3"}), "v3")
        with self.assertRaisesRegex(ValueError, "v1, v2, or v3"):
            runner.eval_resolver({"EVAL_RESOLVER": "v4"})

    def test_artifact_filename_v3_suffix_and_v1_v2_are_unchanged(self):
        self.assertEqual(
            runner.artifact_filename("dataset.jsonl", "opus", "claude", "v3"),
            "bench-dataset-trial-claude-opus-resolver-v3.json",
        )
        self.assertEqual(
            runner.artifact_filename("dataset.jsonl", "opus", "claude", "v1"),
            "bench-dataset-trial-claude-opus.json",
        )
        self.assertEqual(
            runner.artifact_filename("dataset.jsonl", "opus", "claude", "v2"),
            "bench-dataset-trial-claude-opus-resolver-v2.json",
        )
        with self.assertRaisesRegex(ValueError, "v1, v2, or v3"):
            runner.artifact_filename("dataset.jsonl", "opus", "claude", "v4")

    def test_max_provider_attempts_parses_positive_ints_and_treats_absence_as_unlimited(self):
        self.assertIsNone(runner.eval_max_provider_attempts({}))
        self.assertIsNone(runner.eval_max_provider_attempts({"EVAL_MAX_PROVIDER_ATTEMPTS": ""}))
        self.assertEqual(
            runner.eval_max_provider_attempts({"EVAL_MAX_PROVIDER_ATTEMPTS": "50"}), 50
        )
        for bad in ("0", "-1", "not-an-int"):
            with self.subTest(bad=bad), self.assertRaisesRegex(
                ValueError, "EVAL_MAX_PROVIDER_ATTEMPTS"
            ):
                runner.eval_max_provider_attempts({"EVAL_MAX_PROVIDER_ATTEMPTS": bad})

    def test_v3_requires_the_ceiling_but_v1_v2_do_not(self):
        with self.assertRaisesRegex(ValueError, "EVAL_MAX_PROVIDER_ATTEMPTS"):
            runner.require_provider_attempt_ceiling("v3", None)
        self.assertIsNone(runner.require_provider_attempt_ceiling("v3", 50))
        for resolver in ("v1", "v2"):
            self.assertIsNone(runner.require_provider_attempt_ceiling(resolver, None))

    def test_settings_carry_the_ceiling_only_for_v3(self):
        policy = {"resolver": "v1", "context_protocol": "none",
                  "split_manifest_sha256": None, "split": None,
                  "selection_receipt_sha256": None}
        v1_settings = runner.eval_settings("claude", "opus", "sonnet", 4, policy, None)
        self.assertNotIn("max_provider_attempts", v1_settings)

        v3_policy = {**policy, "resolver": "v3"}
        v3_settings = runner.eval_settings("claude", "opus", "sonnet", 4, v3_policy, 50)
        self.assertEqual(v3_settings["max_provider_attempts"], 50)

    def test_checkpoint_interval_is_one_for_v3_and_coarser_otherwise(self):
        self.assertEqual(runner.checkpoint_interval("v3"), 1)
        self.assertEqual(runner.checkpoint_interval("v1"), 25)
        self.assertEqual(runner.checkpoint_interval("v2"), 25)

    def test_provider_usage_requires_incremental_envelope(self):
        valid = {"EVAL_PROVIDER_USAGE_JSON": json.dumps({
            "semantics": "incremental", "usage": {"input_tokens": 3}
        })}
        self.assertEqual(runner.provider_usage(valid), {"input_tokens": 3})
        for value in ({"input_tokens": 3}, {"semantics": "cumulative", "usage": {}}):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "incremental"):
                runner.provider_usage({"EVAL_PROVIDER_USAGE_JSON": json.dumps(value)})
        with self.assertRaisesRegex(ValueError, "numeric"):
            runner.provider_usage({"EVAL_PROVIDER_USAGE_JSON": json.dumps({
                "semantics": "incremental", "usage": {"input_tokens": "many"}
            })})

    def test_no_op_resume_does_not_merge_incremental_usage(self):
        previous = {"input_tokens": 10}
        current = {"input_tokens": 3}
        self.assertEqual(runner.accumulated_usage(previous, current, evaluated_rows=0), previous)
        self.assertEqual(
            runner.accumulated_usage(previous, current, evaluated_rows=1),
            {"input_tokens": 13},
        )

    def test_dataset_and_legacy_rescore_loads_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "data.jsonl"
            dataset.write_text('{"id":"one"}\n')
            with mock.patch.object(runner, "MAX_DATASET_BYTES", 4):
                with self.assertRaisesRegex(ValueError, "dataset too large"):
                    runner.load_dataset(dataset)
            legacy = Path(directory) / "legacy.json"
            legacy.write_text("[]" + " " * 20)
            with mock.patch.object(runner, "MAX_RESCORE_BYTES", 4):
                with self.assertRaisesRegex(ValueError, "artifact too large"):
                    runner.load_rescore(legacy)

    def test_legacy_rescore_allows_missing_or_null_got_as_abstention(self):
        rows = [
            {"id": "missing", "label": "consistent", "category": None},
            {"id": "null", "label": "inconsistent", "category": "direct-mismatch", "got": None},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps(rows))
            loaded = runner.load_rescore(path)

        rescored = metrics.rows_from_transcript(loaded)
        self.assertEqual([row["final_status"] for row in rescored], ["abstain", "abstain"])

    def test_model_cli_capture_bounds_noisy_stdout_and_stderr(self):
        with mock.patch.object(trial, "MAX_MODEL_STDOUT_BYTES", 32), \
             mock.patch.object(trial, "MAX_MODEL_STDERR_BYTES", 32):
            with self.assertRaisesRegex(OSError, "output limit"):
                trial.bounded_cli_run([
                    sys.executable, "-c",
                    "import sys; print('x'*100); print('e'*100, file=sys.stderr)",
                ], capture_output=True, text=True, timeout=2)

    def test_scheduling_never_exceeds_bounded_in_flight_window(self):
        class Future:
            def __init__(self, owner, value):
                self.owner = owner
                self.value = value

            def result(self):
                self.owner.outstanding -= 1
                return self.value

        class Executor:
            def __init__(self):
                self.outstanding = 0
                self.maximum = 0

            def submit(self, function, item):
                self.outstanding += 1
                self.maximum = max(self.maximum, self.outstanding)
                return Future(self, function(item))

        executor = Executor()
        results = list(runner.bounded_results(executor, lambda value: value * 2, range(20), 3))

        self.assertEqual(results, [(value, value * 2) for value in range(20)])
        self.assertEqual(executor.maximum, 3)


if __name__ == "__main__":
    unittest.main()
