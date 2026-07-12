import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from eval.bench import run_bench


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


class ArtifactMetadataTests(unittest.TestCase):
    def test_hashes_inputs_and_captures_commit_cli_and_settings(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            dataset = repo / "dataset.jsonl"
            skill = repo / "skills" / "evergreen" / "SKILL.md"
            judge = repo / "eval" / "bench" / "run_bench.py"
            skill.parent.mkdir(parents=True)
            judge.parent.mkdir(parents=True)
            dataset.write_bytes(b'{"id":"one"}\n')
            skill.write_bytes(b"skill body\n")
            judge.write_bytes(b"judge body\n")

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
        self.assertEqual(metadata["judge"]["sha256"], hashlib.sha256(b"judge body\n").hexdigest())
        self.assertEqual(metadata["git"], git)
        self.assertEqual(metadata["cli_version"], "2.7.1 (Claude Code)")
        self.assertEqual(metadata["settings"], settings)

    def test_metadata_and_document_serialization_are_deterministic(self):
        from eval.bench import artifact

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            dataset = repo / "dataset.jsonl"
            skill = repo / "skills" / "evergreen" / "SKILL.md"
            judge = repo / "eval" / "bench" / "run_bench.py"
            skill.parent.mkdir(parents=True)
            judge.parent.mkdir(parents=True)
            dataset.write_text("data")
            skill.write_text("skill")
            judge.write_text("judge")
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

    def test_hashing_streams_in_bounded_chunks(self):
        from eval.bench import artifact

        reads = []

        class RecordingBytesIO(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                return super().read(size)

        path = mock.MagicMock()
        path.open.return_value.__enter__.return_value = RecordingBytesIO(b"abcdef")
        with mock.patch.object(artifact, "HASH_CHUNK_BYTES", 2):
            digest = artifact.sha256_file(path)

        self.assertEqual(digest, hashlib.sha256(b"abcdef").hexdigest())
        self.assertTrue(reads)
        self.assertNotIn(-1, reads)
        self.assertLessEqual(max(reads), 2)

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


class ArtifactReportTests(unittest.TestCase):
    def metadata(self, dataset="dataset"):
        digest = hashlib.sha256(dataset.encode()).hexdigest()
        return {
            "dataset": {"path": f"{dataset}.jsonl", "sha256": digest},
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
            first = report.render_markdown([python, go], coverage_threshold=1.0)
            second = report.render_markdown([go, python], coverage_threshold=1.0)

        self.assertEqual(first, second)
        self.assertLess(first.index("## go"), first.index("## python"))
        self.assertEqual(first.count("| Attempted | 2 |"), 2)
        self.assertNotIn("all languages", first.lower())
        self.assertNotIn("aggregate", first.lower())
        self.assertIn("### Provenance", first)
        self.assertIn("claude 1.0", first)

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
                str(artifact_path), "--markdown", str(markdown), "--coverage-threshold", "0.75"
            ])
            text = markdown.read_text()

        self.assertEqual(status, 2)
        self.assertIn("Coverage: **50.0%**", text)
        self.assertIn("FAIL", text)

    def test_cli_accepts_coverage_at_threshold(self):
        from eval.bench import report

        rows = [completed("p1", "python", "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            artifact_path = self.write_artifact(directory, "artifact.json", rows)
            markdown = Path(directory) / "report.md"
            status = report.main([str(artifact_path), "--markdown", str(markdown)])

        self.assertEqual(status, 0)

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
            status = report.main([str(first), str(second), "--markdown", str(markdown)])
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
            status = report.main([str(first), str(second), "--markdown", str(markdown)])

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
            status = report.main([str(first), str(second), "--markdown", str(markdown)])
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
            self.assertEqual(report.main([str(legacy), "--markdown", str(markdown)]), 2)
            self.assertIn("legacy", markdown.read_text().lower())

            bad = self.metadata("bad")
            bad["cli_version"] = "unavailable"
            artifact_path = self.write_artifact(directory, "bad.json", rows, bad)
            self.assertEqual(report.main([
                str(artifact_path), "--markdown", str(markdown)
            ]), 2)
            self.assertIn("unavailable", markdown.read_text().lower())

            bad_hash = self.metadata("bad-hash")
            bad_hash["skill"]["sha256"] = "not-a-hash"
            artifact_path = self.write_artifact(directory, "bad-hash.json", rows, bad_hash)
            self.assertEqual(report.main([
                str(artifact_path), "--markdown", str(markdown)
            ]), 2)
            self.assertIn("invalid", markdown.read_text().lower())

    def test_report_bounds_artifact_count_size_and_rows(self):
        from eval.bench import report

        rows = [completed("p1", "python", "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_artifact(directory, "one.json", rows)
            with mock.patch.object(report, "MAX_ARTIFACTS", 1):
                with self.assertRaisesRegex(ValueError, "too many artifacts"):
                    report.render_markdown([path, path])
            with mock.patch.object(report, "MAX_ARTIFACT_BYTES", 10):
                with self.assertRaisesRegex(ValueError, "too large"):
                    report.render_markdown([path])
            with mock.patch.object(report, "MAX_ROWS", 0):
                with self.assertRaisesRegex(ValueError, "too many rows"):
                    report.render_markdown([path])

    def test_language_heading_is_markdown_safe(self):
        from eval.bench import report

        malicious = "python\n# injected <script>"
        rows = [completed("p1", malicious, "consistent", None, "consistent")]
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_artifact(directory, "one.json", rows)
            text = report.render_markdown([path])

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
            self.assertEqual(report.main([str(path), "--markdown", str(markdown)]), 2)
            self.assertIn("timing", markdown.read_text().lower())

            document["timing"] = {"started_at": "2026-01-01T00:00:00Z", "elapsed_seconds": 1}
            document["rows"][0]["language"] = ["python"]
            path.write_text(json.dumps(document))
            self.assertEqual(report.main([str(path), "--markdown", str(markdown)]), 2)
            self.assertIn("language", markdown.read_text().lower())


class RunBenchArtifactIntegrationTests(unittest.TestCase):
    def test_artifact_rows_supports_new_envelope_and_legacy_transcript(self):
        rows = [completed("p1", "python", "consistent", None, "consistent")]
        self.assertEqual(run_bench.artifact_rows({"schema_version": 1, "rows": rows}), rows)
        self.assertEqual(run_bench.artifact_rows(rows), rows)

    def test_concurrency_is_strictly_bounded(self):
        for value in ("0", "33", "not-an-int"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "EVAL_CONCURRENCY"):
                run_bench.eval_concurrency({"EVAL_CONCURRENCY": value})
        self.assertEqual(run_bench.eval_concurrency({"EVAL_CONCURRENCY": "32"}), 32)


if __name__ == "__main__":
    unittest.main()
