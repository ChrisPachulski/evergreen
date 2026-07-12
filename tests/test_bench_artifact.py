import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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

            def command_result(command, **_kwargs):
                if command[:2] == ["git", "-C"]:
                    return SimpleNamespace(returncode=0, stdout="abc123\n")
                self.assertEqual(command, ["claude", "--version"])
                return SimpleNamespace(returncode=0, stdout="2.7.1 (Claude Code)\n")

            settings = {"models": {"strong": "opus", "cheap": "sonnet"}, "concurrency": 2}
            with mock.patch("eval.bench.artifact.subprocess.run", side_effect=command_result):
                metadata = artifact.artifact_metadata(dataset, repo, settings)

        self.assertEqual(metadata["dataset"]["sha256"], hashlib.sha256(b'{"id":"one"}\n').hexdigest())
        self.assertEqual(metadata["skill"]["sha256"], hashlib.sha256(b"skill body\n").hexdigest())
        self.assertEqual(metadata["judge"]["sha256"], hashlib.sha256(b"judge body\n").hexdigest())
        self.assertEqual(metadata["git_commit"], "abc123")
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
            result = SimpleNamespace(returncode=0, stdout="fixed\n")
            with mock.patch("eval.bench.artifact.subprocess.run", return_value=result):
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


class ArtifactReportTests(unittest.TestCase):
    def write_artifact(self, directory, name, rows):
        path = Path(directory) / name
        path.write_text(json.dumps({"schema_version": 1, "metadata": {}, "rows": rows}))
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


class RunBenchArtifactIntegrationTests(unittest.TestCase):
    def test_artifact_rows_supports_new_envelope_and_legacy_transcript(self):
        rows = [completed("p1", "python", "consistent", None, "consistent")]
        self.assertEqual(run_bench.artifact_rows({"schema_version": 1, "rows": rows}), rows)
        self.assertEqual(run_bench.artifact_rows(rows), rows)


if __name__ == "__main__":
    unittest.main()
