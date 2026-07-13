import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from eval.bench import artifact, publication, report


EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def metadata(dataset_path="eval/bench/input.jsonl", dataset_sha=None):
    return {
        "dataset": {"path": dataset_path, "sha256": dataset_sha or "1" * 64},
        "provider": "codex",
        "skill": {"path": "skills/evergreen/SKILL.md", "sha256": "2" * 64},
        "judge": {
            "path": "eval/bench/run_bench.py",
            "sha256": "3" * 64,
            "files": [{"path": "eval/bench/run_bench.py", "sha256": "4" * 64}],
        },
        "git": {
            "commit": "5" * 40,
            "tree": "6" * 40,
            "dirty": False,
            "status_sha256": EMPTY_SHA,
            "diff_sha256": EMPTY_SHA,
            "untracked_sha256": EMPTY_SHA,
        },
        "cli_version": "codex-cli 0.144.1",
        "settings": {
            "provider": "codex",
            "models": {"strong": "gpt-5.6-sol", "cheap": "gpt-5.6-sol"},
            "concurrency": 4,
        },
    }


def private_row(identifier="one", language="rust"):
    def ok(value):
        return {"status": "ok", "value": value}

    return {
        "id": identifier,
        "func": "execute",
        "language": language,
        "code": "SECRET_SHAPED_SOURCE = 'do-not-publish'",
        "doc": "free-form documentation must not be copied",
        "label": "consistent",
        "category": None,
        "unexpected_source_field": "must not pass through",
        "got": {
            "final_status": "complete",
            "final_verdict": "consistent",
            "verdict": "consistent",
            "category": None,
            "why": "free-form final explanation",
            "contested": False,
            "unexpected_result_field": "must not pass through",
            "stages": {
                "snap": ok({
                    "id": identifier,
                    "verdict": "consistent",
                    "category": None,
                    "why": "free-form snap explanation",
                    "unexpected": "drop",
                }),
                "challenge": ok({"cracks": False, "why": "free-form challenge"}),
                "prongs": [
                    ok({"role": role, "verdict": "consistent", "why": "free-form prong"})
                    for role in ("defend", "prove-wrong", "hardest-broken")
                ],
                "blindspot": ok({"missed_angle": "free-form candidate"}),
            },
        },
    }


def private_artifact(rows):
    return {
        "schema_version": 1,
        "metadata": metadata(),
        "timing": {"started_at": "2026-07-12T20:00:00Z", "elapsed_seconds": 1.25},
        "rows": rows,
    }


class ProjectionTests(unittest.TestCase):
    def test_projection_keeps_structured_decisions_and_drops_all_free_text(self):
        projected = publication.project_artifact(private_artifact([private_row()]))
        row = projected["rows"][0]

        self.assertEqual(set(row), {"id", "language", "label", "category", "got", "trial"})
        self.assertEqual(set(row["got"]), {
            "final_status", "final_verdict", "verdict", "category", "contested",
        })
        self.assertEqual(row["trial"]["snap"], {
            "status": "ok", "verdict": "consistent", "category": None,
        })
        self.assertEqual(row["trial"]["challenge"], {"status": "ok", "cracks": False})
        self.assertEqual(
            [item["role"] for item in row["trial"]["prongs"]],
            ["defend", "prove-wrong", "hardest-broken"],
        )
        self.assertEqual(row["trial"]["blindspot"], {
            "status": "ok", "missed_angle_present": True,
        })
        encoded = json.dumps(projected, sort_keys=True)
        public_keys = set()

        def collect_keys(value):
            if isinstance(value, dict):
                public_keys.update(value)
                for item in value.values():
                    collect_keys(item)
            elif isinstance(value, list):
                for item in value:
                    collect_keys(item)

        collect_keys(projected["rows"])
        self.assertTrue({"code", "doc", "func", "why", "reason", "missed_angle"}.isdisjoint(
            public_keys
        ))
        for forbidden in (
            "SECRET_SHAPED_SOURCE", "free-form", "unexpected_source_field",
            "unexpected_result_field",
        ):
            self.assertNotIn(forbidden, encoded)

    def test_projection_preserves_escalation_synthesis_and_abstention_status(self):
        row = private_row()
        row["got"]["final_status"] = "abstain"
        row["got"]["final_verdict"] = None
        row["got"]["verdict"] = None
        row["got"]["stages"]["prongs"][1] = {
            "status": "abstain", "reason": "malformed provider output",
        }
        row["got"]["stages"]["prongs_escalated"] = row["got"]["stages"]["prongs"]
        row["got"]["stages"]["synthesis"] = {
            "status": "abstain", "reason": "timeout /private/path",
        }

        projected = publication.project_row(row)

        self.assertEqual(projected["got"]["final_status"], "abstain")
        self.assertEqual(projected["trial"]["prongs"][1], {"status": "abstain"})
        self.assertEqual(projected["trial"]["synthesis"], {"status": "abstain"})
        self.assertNotIn("/private/path", json.dumps(projected))

    def test_projection_rejects_unknown_stage_and_invalid_types(self):
        row = private_row()
        row["got"]["stages"]["invented"] = {"status": "ok", "value": {}}
        with self.assertRaisesRegex(ValueError, "unknown trial stage"):
            publication.project_row(row)

        row = private_row()
        row["got"]["contested"] = "false"
        with self.assertRaisesRegex(ValueError, "contested"):
            publication.project_row(row)

    def test_serialization_is_stable_and_invalid_envelopes_fail(self):
        first = publication.canonical_bytes({"z": 1, "a": {"y": 2, "x": 3}})
        second = publication.canonical_bytes({"a": {"x": 3, "y": 2}, "z": 1})
        self.assertEqual(first, second)
        self.assertTrue(first.endswith(b"\n"))

        with self.assertRaisesRegex(ValueError, "schema"):
            publication.project_artifact([])
        broken = private_artifact([private_row()])
        broken["timing"]["elapsed_seconds"] = float("nan")
        with self.assertRaisesRegex(ValueError, "timing"):
            publication.project_artifact(broken)


class PublicationTests(unittest.TestCase):
    def make_source_package(self, root):
        dataset_path = root / "eval/bench/input.jsonl"
        dataset_path.parent.mkdir(parents=True)
        source_row = private_row()
        source_row.pop("unexpected_source_field")
        dataset_row = {key: value for key, value in source_row.items() if key != "got"}
        dataset_payload = (json.dumps(dataset_row, sort_keys=True) + "\n").encode()
        dataset_path.write_bytes(dataset_payload)
        document = private_artifact([source_row])
        document["metadata"] = metadata(
            dataset_sha=hashlib.sha256(dataset_payload).hexdigest()
        )
        source = root / "source.json"
        source.write_bytes(publication.canonical_bytes(document))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        report_path = root / "report.md"
        report_path.write_text(report.render_markdown([source], ["rust"], 0.99))
        return source, digest, report_path

    def make_git_publication(self, base):
        repo = base / "repo"
        repo.mkdir()
        subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"],
                       check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)

        source_row = private_row()
        source_row.pop("unexpected_source_field")
        dataset_row = {key: value for key, value in source_row.items() if key != "got"}
        dataset_path = repo / "eval/bench/input.jsonl"
        dataset_path.parent.mkdir(parents=True)
        dataset_payload = (json.dumps(dataset_row, sort_keys=True) + "\n").encode()
        dataset_path.write_bytes(dataset_payload)
        skill_path = repo / "skills/evergreen/SKILL.md"
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("# Test skill\n")
        bench = repo / "eval/bench"
        for name in artifact.JUDGE_MODULES:
            path = bench / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"{name}\n")
        subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "frozen inputs"], check=True)

        run_metadata = metadata(
            dataset_sha=hashlib.sha256(dataset_payload).hexdigest()
        )
        run_metadata["skill"] = {
            "path": "skills/evergreen/SKILL.md",
            "sha256": artifact.sha256_file(skill_path),
        }
        run_metadata["judge"] = artifact.judge_identity(repo)
        run_metadata["git"] = artifact.git_identity(repo)
        document = private_artifact([source_row])
        document["metadata"] = run_metadata
        source = base / "source.json"
        source.write_bytes(publication.canonical_bytes(document))
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        report_path = repo / "eval/bench/results-0.4.0.md"
        report_path.write_text(report.render_markdown([source], ["rust"], 0.99))
        manifest_path = publication.export_publication(
            [(digest, source)], repo / "eval/bench/public/0.4.0", "0.4.0", ["rust"],
            0.99, report_path, repo,
        )
        return repo, manifest_path, report_path

    def test_parse_source_requires_lowercase_sha256(self):
        digest, path = publication.parse_source("a" * 64 + "=/tmp/source.json")
        self.assertEqual(digest, "a" * 64)
        self.assertEqual(path, Path("/tmp/source.json"))
        for value in ("missing", "xyz=/tmp/source", "A" * 64 + "=/tmp/source"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "source"):
                publication.parse_source(value)

    def test_export_rejects_source_hash_mismatch_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(json.dumps(private_artifact([private_row()])))
            output = root / "public"
            with self.assertRaisesRegex(ValueError, "source artifact SHA-256"):
                publication.export_publication(
                    [("0" * 64, source)], output, "0.4.0", ["rust"], 0.99,
                    root / "report.md", root,
                )
            self.assertFalse(output.exists())

    def test_export_refuses_symlink_source_and_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(json.dumps(private_artifact([private_row()])))
            link = root / "source-link.json"
            link.symlink_to(source)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with self.assertRaisesRegex(ValueError, "regular file"):
                publication.export_publication(
                    [(digest, link)], root / "public", "0.4.0", ["rust"], 0.99,
                    root / "report.md", root,
                )

            output = root / "public"
            output.mkdir()
            with self.assertRaisesRegex(ValueError, "already exists"):
                publication.export_publication(
                    [(digest, source)], output, "0.4.0", ["rust"], 0.99,
                    root / "report.md", root,
                )

    def test_export_writes_deterministic_manifest_and_rescorable_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, digest, report_path = self.make_source_package(root)
            output = root / "eval/bench/public/0.4.0"

            manifest_path = publication.export_publication(
                [(digest, source)], output, "0.4.0", ["rust"], 0.99,
                report_path, root,
            )

            manifest = json.loads(manifest_path.read_text())
            self.assertEqual(manifest["evaluated_release"], "0.4.0")
            self.assertEqual(manifest["publication"], {
                "coverage_threshold": 0.99, "required_languages": ["rust"],
            })
            self.assertEqual(manifest["artifacts"][0]["source"]["sha256"], digest)
            self.assertNotIn(str(root), manifest_path.read_text())
            public_path = root / manifest["artifacts"][0]["path"]
            public_document = json.loads(public_path.read_text())
            self.assertNotIn("code", public_document["rows"][0])
            rescored = report.render_markdown([public_path], ["rust"], 0.99)
            self.assertEqual(rescored, report_path.read_text())

            with self.assertRaisesRegex(ValueError, "already exists"):
                publication.export_publication(
                    [(digest, source)], output, "0.4.0", ["rust"], 0.99,
                    report_path, root,
                )

    def test_verify_accepts_complete_package_and_rejects_public_free_text(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, manifest_path, report_path = self.make_git_publication(Path(directory))
            paths = publication.verify_publication(manifest_path, repo, report_path)
            self.assertEqual(len(paths), 1)

            manifest = json.loads(manifest_path.read_text())
            public_path = repo / manifest["artifacts"][0]["path"]
            value = json.loads(public_path.read_text())
            value["rows"][0]["got"]["why"] = "must not become public"
            public_path.write_bytes(publication.canonical_bytes(value))
            manifest["artifacts"][0]["bytes"] = public_path.stat().st_size
            manifest["artifacts"][0]["sha256"] = hashlib.sha256(
                public_path.read_bytes()
            ).hexdigest()
            manifest_path.write_bytes(publication.canonical_bytes(manifest))

            with self.assertRaisesRegex(ValueError, "public artifact projection"):
                publication.verify_publication(manifest_path, repo, report_path)

    def test_verify_rejects_manifest_dataset_report_and_git_tampering(self):
        cases = ("artifact", "dataset", "report", "tree")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as directory:
                repo, manifest_path, report_path = self.make_git_publication(Path(directory))
                manifest = json.loads(manifest_path.read_text())
                if case == "artifact":
                    path = repo / manifest["artifacts"][0]["path"]
                    path.write_bytes(path.read_bytes() + b" ")
                    message = "public artifact bytes"
                elif case == "dataset":
                    path = repo / manifest["artifacts"][0]["dataset"]["path"]
                    path.write_bytes(path.read_bytes() + b" ")
                    message = "dataset SHA-256"
                elif case == "report":
                    report_path.write_text("changed\n")
                    message = "report SHA-256"
                else:
                    manifest["provenance"]["tree"] = "0" * 40
                    manifest_path.write_bytes(publication.canonical_bytes(manifest))
                    message = "provenance summary"
                with self.assertRaisesRegex(ValueError, message):
                    publication.verify_publication(manifest_path, repo, report_path)

    def test_historical_verifier_rejects_tree_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, manifest_path, _report_path = self.make_git_publication(Path(directory))
            manifest = json.loads(manifest_path.read_text())
            public_path = repo / manifest["artifacts"][0]["path"]
            run_metadata = json.loads(public_path.read_text())["metadata"]
            run_metadata["git"]["tree"] = "0" * 40
            with self.assertRaisesRegex(ValueError, "Git tree"):
                publication._verify_historical_provenance(repo, run_metadata)

    def test_verify_cli_reports_success_and_structural_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            repo, manifest_path, report_path = self.make_git_publication(Path(directory))
            script = Path(publication.__file__).resolve()
            command = [
                sys.executable, str(script), "verify", "--manifest", str(manifest_path),
                "--repo", str(repo), "--report", str(report_path),
            ]
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout.strip(), "verified public benchmark publication: 1 artifacts"
            )

            manifest_path.write_text("not json\n")
            completed = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2)
            self.assertIn("publication error:", completed.stderr)
            self.assertNotIn("not json", completed.stderr)
