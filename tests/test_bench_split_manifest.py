import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


from eval.bench import make_split, split_manifest
from eval.bench.split_manifest import load_split_assignments, load_split_manifest
from eval.oracle.split import POLICY_SHA256


def dataset_row(pair_id, language="Java", label="consistent"):
    return {
        "id": pair_id,
        "func": "f",
        "code": "return 1",
        "doc": "returns one",
        "label": label,
        "category": None,
        "language": language,
    }


class SplitManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.rows = [dataset_row("org/a/f#1"), dataset_row("org/b/g#1", label="inconsistent")]
        self.dataset = self.root / "java.jsonl"
        payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in self.rows)
        self.dataset.write_text(payload)
        self.digest = hashlib.sha256(payload.encode()).hexdigest()

    def tearDown(self):
        self.directory.cleanup()

    def write_manifest(self, rows=None, **changes):
        document = {
            "schema_version": 1,
            "datasets": [{"sha256": self.digest, "language": "Java"}],
            "rows": rows or [
                {"id": "org/a/f#1", "dataset_sha256": self.digest,
                 "project": "org/a", "split": "dev"},
                {"id": "org/b/g#1", "dataset_sha256": self.digest,
                 "project": "org/b", "split": "holdout"},
            ],
        }
        document.update(changes)
        path = self.root / "split.json"
        path.write_text(json.dumps(document))
        return path

    def write_votes(self):
        from eval.bench import validate_labels

        binding = validate_labels._vote_binding(
            self.dataset.read_bytes(), cli_version="claude test",
            cli_executable_sha256="a" * 64,
        )
        votes = {
            row["id"]: {model: row["label"] for model in validate_labels.ANNOTATORS}
            for row in self.rows
        }
        path = self.root / "screen.votes.json"
        validate_labels._write_votes(path, binding, votes)
        return path

    def test_accepts_complete_project_grouped_id_only_manifest(self):
        manifest = self.write_manifest()
        result = load_split_manifest(manifest, [self.dataset])
        self.assertEqual(result, {"org/a/f#1": "dev", "org/b/g#1": "holdout"})
        self.assertEqual(load_split_assignments(manifest), result)

    def test_byte_loader_validates_the_supplied_manifest_snapshot(self):
        manifest = self.write_manifest()
        frozen = manifest.read_bytes()
        manifest.write_text("{}")

        assignments, row_datasets, declarations = \
            split_manifest.load_split_bindings_bytes(frozen)

        self.assertEqual(assignments, {"org/a/f#1": "dev", "org/b/g#1": "holdout"})
        self.assertEqual(set(row_datasets.values()), {self.digest})
        self.assertEqual(declarations, {(self.digest, "Java")})

    def test_full_validator_does_not_reopen_manifest_after_capture(self):
        manifest = self.write_manifest()
        with mock.patch.object(
            split_manifest, "_manifest", side_effect=AssertionError("reopened")
        ):
            self.assertEqual(
                load_split_manifest(manifest, [self.dataset]),
                {"org/a/f#1": "dev", "org/b/g#1": "holdout"},
            )

    def test_splitter_accepts_three_component_codocbench_ids(self):
        self.assertEqual(make_split.repository("owner/repo/function#3-old"), "owner/repo")
        with self.assertRaisesRegex(ValueError, "id shape"):
            make_split.repository("owner/repo")

    def test_bound_subset_manifest_keeps_only_parent_assigned_split_rows(self):
        from eval.bench import bind_subset

        parent = self.write_manifest()
        subset = self.root / "validated-dev.jsonl"
        subset.write_text(json.dumps(self.rows[0], sort_keys=True) + "\n")
        document = bind_subset.build_manifest(
            subset, [self.dataset], parent, "dev", vote_ledger=self.write_votes()
        )
        output = self.root / "validated-dev-manifest.json"
        output.write_text(json.dumps(document))

        self.assertEqual(load_split_manifest(output, [subset]), {"org/a/f#1": "dev"})
        with self.assertRaisesRegex(ValueError, "declared dev split"):
            bind_subset.build_manifest(
                self.dataset, [self.dataset], parent, "dev",
                vote_ledger=self.write_votes(),
            )

    def test_screen_receipt_binds_parent_votes_output_and_manifest(self):
        from eval.bench import bind_subset

        parent = self.write_manifest()
        votes = self.write_votes()
        subset = self.root / "validated-dev.jsonl"
        subset.write_text(json.dumps(self.rows[0], sort_keys=True) + "\n")
        document = bind_subset.build_manifest(
            subset, [self.dataset], parent, "dev", vote_ledger=votes
        )

        receipt = bind_subset.build_screen_receipt(
            subset, self.dataset, parent, votes, "dev", document
        )

        self.assertEqual(receipt["output_dataset_sha256"], hashlib.sha256(
            subset.read_bytes()
        ).hexdigest())
        self.assertEqual(receipt["parent_dataset_sha256"], self.digest)
        self.assertEqual(receipt["vote_ledger_sha256"], hashlib.sha256(
            votes.read_bytes()
        ).hexdigest())
        self.assertEqual(receipt["rows"], 1)

    def test_bound_subset_rejects_mutated_parent_rows(self):
        from eval.bench import bind_subset

        parent = self.write_manifest()
        subset = self.root / "forged-dev.jsonl"
        subset.write_text(json.dumps({**self.rows[0], "code": "FORGED"}) + "\n")

        with self.assertRaisesRegex(ValueError, "match the parent dataset"):
            bind_subset.build_manifest(
                subset, [self.dataset], parent, "dev",
                vote_ledger=self.write_votes(),
            )

    def test_bound_subset_rejects_post_screen_cherry_picking(self):
        from eval.bench import bind_subset

        parent = self.write_manifest(rows=[
            {"id": row["id"], "dataset_sha256": self.digest,
             "project": make_split.repository(row["id"]), "split": "dev"}
            for row in self.rows
        ])
        subset = self.root / "cherry-picked.jsonl"
        subset.write_text(json.dumps(self.rows[0], sort_keys=True) + "\n")

        with self.assertRaisesRegex(ValueError, "retained set"):
            bind_subset.build_manifest(
                subset, [self.dataset], parent, "dev",
                vote_ledger=self.write_votes(),
            )

    def test_bound_subset_can_reproduce_a_predeclared_exclusion(self):
        from eval.bench import bind_subset

        parent = self.write_manifest(rows=[
            {"id": row["id"], "dataset_sha256": self.digest,
             "project": make_split.repository(row["id"]), "split": "dev"}
            for row in self.rows
        ])
        subset = self.root / "eligible.jsonl"
        subset.write_text(json.dumps(self.rows[0], sort_keys=True) + "\n")

        document = bind_subset.build_manifest(
            subset, [self.dataset], parent, "dev",
            excluded_ids={self.rows[1]["id"]},
        )

        self.assertEqual([row["id"] for row in document["rows"]], [self.rows[0]["id"]])

    def test_rejects_project_leakage_between_splits(self):
        rows = [
            {"id": "org/a/f#1", "dataset_sha256": self.digest,
             "project": "org/a", "split": "dev"},
            {"id": "org/b/g#1", "dataset_sha256": self.digest,
             "project": "org/a", "split": "holdout"},
        ]
        with self.assertRaisesRegex(ValueError, "invalid.*project"):
            load_split_manifest(self.write_manifest(rows), [self.dataset])

    def test_rejects_incomplete_unknown_and_duplicate_rows(self):
        valid = {"id": "org/a/f#1", "dataset_sha256": self.digest,
                 "project": "org/a", "split": "dev"}
        cases = {
            "incomplete": [valid],
            "unknown": [valid, {"id": "org/x/z#1", "dataset_sha256": self.digest,
                                 "project": "org/x", "split": "holdout"}],
            "duplicate": [valid, valid],
        }
        for name, rows in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_split_manifest(self.write_manifest(rows), [self.dataset])

    def test_rejects_wrong_hash_unknown_split_and_forbidden_fields(self):
        base = [
            {"id": "org/a/f#1", "dataset_sha256": self.digest,
             "project": "org/a", "split": "dev"},
            {"id": "org/b/g#1", "dataset_sha256": self.digest,
             "project": "org/b", "split": "holdout"},
        ]
        variants = []
        wrong_hash = [dict(item) for item in base]
        wrong_hash[0]["dataset_sha256"] = "0" * 64
        variants.append(wrong_hash)
        wrong_split = [dict(item) for item in base]
        wrong_split[0]["split"] = "test"
        variants.append(wrong_split)
        forbidden = [dict(item) for item in base]
        forbidden[0]["label"] = "consistent"
        variants.append(forbidden)
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                load_split_manifest(self.write_manifest(rows), [self.dataset])

    def test_rejects_manifest_dataset_hash_or_language_mismatch(self):
        with self.assertRaisesRegex(ValueError, "dataset declarations"):
            load_split_manifest(
                self.write_manifest(datasets=[{"sha256": "0" * 64, "language": "Java"}]),
                [self.dataset],
            )
        with self.assertRaisesRegex(ValueError, "dataset declarations"):
            load_split_manifest(
                self.write_manifest(datasets=[{"sha256": self.digest, "language": "Python"}]),
                [self.dataset],
            )
    def test_cli_reports_only_counts(self):
        completed = subprocess.run(
            [sys.executable, "-m", "eval.bench.split_manifest",
             str(self.write_manifest()), str(self.dataset)],
            cwd=Path(__file__).parents[1], capture_output=True, text=True, check=True,
        )
        self.assertEqual(
            completed.stdout,
            "split manifest valid: 2 rows; 2 projects do not cross dev/holdout\n",
        )
        self.assertNotIn("returns one", completed.stdout)

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        valid = self.write_manifest().read_text()
        for name, raw in (
            ("duplicate", valid.replace('"schema_version": 1',
                                        '"schema_version": 1, "schema_version": 1', 1)),
            ("nonfinite", valid.replace('"schema_version": 1',
                                        '"schema_version": NaN', 1)),
        ):
            with self.subTest(name=name):
                path = self.root / f"strict-{name}.json"
                path.write_text(raw)
                with self.assertRaisesRegex(ValueError, "valid JSON"):
                    load_split_manifest(path, [self.dataset])

    def test_accepts_hash_bound_oracle_v2_without_opening_packages_for_assignments(self):
        packages = []
        declarations = []
        public_rows = []
        for split, row_id in (("dev", "oracle-" + "a" * 64),
                              ("holdout", "oracle-" + "b" * 64)):
            path = self.root / f"{split}.jsonl"
            row = {"id": row_id, "split": split, "language": "python",
                   "code": "return 1", "documentation": "returns one",
                   "label": "consistent"}
            raw = json.dumps(row, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            packages.append(path)
            declarations.append({"sha256": digest, "split": split, "rows": 1})
            declarations[-1]["path_sha256"] = hashlib.sha256(
                str(path.absolute()).encode()
            ).hexdigest()
            public_rows.append({"id": row_id, "dataset_sha256": digest, "split": split})
        document = {
            "schema_version": 2,
            "similarity_policy_sha256": POLICY_SHA256,
            "reference_corpus_sha256": "f" * 64,
            "subject_commit": "1" * 40,
            "subject_tree": "2" * 40,
            "datasets": declarations,
            "rows": public_rows,
        }
        manifest = self.root / "oracle-split.json"
        manifest.write_text(json.dumps(document))

        expected = {row["id"]: row["split"] for row in public_rows}
        self.assertEqual(load_split_assignments(manifest), expected)
        self.assertEqual(load_split_manifest(manifest, packages), expected)

    def test_oracle_v2_rejects_leaks_policy_drift_and_package_mismatch(self):
        row_id = "oracle-" + "a" * 64
        package = self.root / "dev.jsonl"
        raw = json.dumps({"id": row_id, "split": "dev", "language": "python"}).encode() + b"\n"
        package.write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        base = {
            "schema_version": 2,
            "similarity_policy_sha256": POLICY_SHA256,
            "reference_corpus_sha256": "f" * 64,
            "subject_commit": "1" * 40,
            "subject_tree": "2" * 40,
            "datasets": [
                {"sha256": digest, "path_sha256": hashlib.sha256(
                    str(package.absolute()).encode()
                ).hexdigest(), "split": "dev", "rows": 1},
                {"sha256": "d" * 64, "path_sha256": "e" * 64,
                 "split": "holdout", "rows": 0},
            ],
            "rows": [{"id": row_id, "dataset_sha256": digest, "split": "dev"}],
        }
        for name, change in (
            ("leak", lambda value: value["rows"][0].__setitem__("project", "org/private")),
            ("policy", lambda value: value.__setitem__("similarity_policy_sha256", "d" * 64)),
            ("count", lambda value: value["datasets"][0].__setitem__("rows", 2)),
        ):
            with self.subTest(name=name):
                document = json.loads(json.dumps(base))
                change(document)
                manifest = self.root / f"bad-{name}.json"
                manifest.write_text(json.dumps(document))
                with self.assertRaises(ValueError):
                    load_split_assignments(manifest)

        manifest = self.root / "mismatch.json"
        manifest.write_text(json.dumps(base))
        with self.assertRaisesRegex(ValueError, "package declarations"):
            load_split_manifest(manifest, [package])


class CandidateProvenanceTests(unittest.TestCase):
    def test_v2_candidate_provenance_binds_generators_manifests_and_partitions(self):
        root = Path(__file__).parents[1]
        path = root / "eval/bench/codocbench-v2-candidate-provenance.json"
        provenance = json.loads(path.read_text())
        self.assertEqual(provenance["schema_version"], 1)
        self.assertIs(provenance["holdout_available"], False)
        self.assertEqual(
            provenance["prior_screen_status"],
            "canonical-id-exposed-results-excluded",
        )
        self.assertEqual(
            {row["language"] for row in provenance["languages"]},
            {"Python", "typescript", "rust", "go"},
        )
        for key in ("converter", "splitter", "subset_binder"):
            source = root / provenance["generator"][f"{key}_path"]
            self.assertEqual(
                hashlib.sha256(source.read_bytes()).hexdigest(),
                provenance["generator"][f"{key}_sha256"],
            )
        mining_path = provenance["generator"]["mining_path"]
        mining_commit = provenance["generator"]["mining_source_commit"]
        mining_source = subprocess.run(
            ["git", "show", f"{mining_commit}:{mining_path}"], cwd=root,
            capture_output=True, check=True,
        ).stdout
        self.assertEqual(
            hashlib.sha256(mining_source).hexdigest(),
            provenance["generator"]["mining_source_sha256"],
        )
        exposure_path = root / provenance["exposure_inventory_path"]
        self.assertEqual(
            hashlib.sha256(exposure_path.read_bytes()).hexdigest(),
            provenance["exposure_inventory_sha256"],
        )
        exposure = json.loads(exposure_path.read_text())["samples"]
        language_key = {"Python": "Python", "typescript": "TypeScript",
                        "rust": "Rust", "go": "Go"}
        for lane in provenance["languages"]:
            pool = lane["candidate_pool"]
            split = lane["split"]
            manifest_path = root / split["manifest_path"]
            self.assertEqual(
                hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                split["manifest_sha256"],
            )
            assignments = load_split_assignments(manifest_path)
            self.assertEqual(len(assignments), pool["rows"])
            document = json.loads(manifest_path.read_text())
            declared = {item["sha256"] for item in document["datasets"]}
            self.assertEqual(
                declared,
                {cell["sha256"] for cell in split["assignment_partitions"].values()},
            )
            sample = exposure[language_key[lane["language"]]]
            self.assertEqual(len(sample["ids"]), 20)
            self.assertEqual(len(set(sample["ids"])), 20)
            self.assertEqual(
                hashlib.sha256("\n".join(sample["ids"]).encode()).hexdigest(),
                sample["sample_ids_sha256"],
            )
            self.assertEqual(set(sample["ids"]) <= set(assignments), True)
            self.assertEqual(split["target_development_fraction"], 0.6)
            self.assertEqual(
                split["excluded_partition_status"],
                "pre-screen-exposed-development-only",
            )
            for name, cell in split["assignment_partitions"].items():
                self.assertEqual(
                    sum(value == name for value in assignments.values()), cell["rows"]
                )
                self.assertEqual(cell["consistent"] + cell["inconsistent"], cell["rows"])
            for name, cell in split["eligible_partitions"].items():
                eligible_path = root / cell["manifest_path"]
                self.assertEqual(
                    hashlib.sha256(eligible_path.read_bytes()).hexdigest(),
                    cell["manifest_sha256"],
                )
                eligible = load_split_assignments(eligible_path)
                self.assertEqual(set(eligible.values()), {name})
                self.assertEqual(len(eligible), cell["rows"])
                self.assertFalse(set(eligible) & set(sample["ids"]))
                expected = {
                    pair_id for pair_id, assigned in assignments.items()
                    if assigned == name and pair_id not in set(sample["ids"])
                }
                self.assertEqual(set(eligible), expected)
                self.assertTrue(all(assignments[pair_id] == name for pair_id in eligible))
                eligible_document = json.loads(eligible_path.read_text())
                self.assertEqual(
                    {item["sha256"] for item in eligible_document["datasets"]},
                    {cell["sha256"]},
                )
            if lane["source"]["kind"] == "mined-coupled-changes":
                payload = ("\n".join(lane["source"]["repositories"]) + "\n").encode()
                self.assertEqual(
                    hashlib.sha256(payload).hexdigest(),
                    lane["source"]["repositories_sha256"],
                )

    def test_v2_screened_selections_bind_checked_in_manifests(self):
        from eval.bench import validate_labels

        root = Path(__file__).parents[1]
        expected = {
            "python": (294,
                       "0beae54f20260abcb032ac03fd64f3459a250a449754226cfc14a1b61e12adb7",
                       "1bc19f793d2a827976c8dd706671953e7d501caaf4a43b685d449cfcee71081f"),
            "typescript": (139,
                           "7da2513472bef7b0bd13827679f33879a76a0e3ba517fcfbbd6cbe7f2838bd49",
                           "5ee16abce076bb3ec0dcfd009811ca0677b895923076beaf74bf8c5a1c03393d"),
            "rust": (251,
                     "d28c8faebcd3cd37cd1fda564ca3a70d794d1f414d4ac547c3b9e1ef9ec279a2",
                     "1e070d0c91eb3b791241d7c2b514d5370d5196da9bab1ff58e7053fa476f88af"),
            "go": (346,
                   "1ba6107a243e894d9527b28887003209e4b8f697b650efb123db738def83bbac",
                   "c77d8e0c3c5190fdf8a29a465e73f3f9d644ae02aa3cdd28729db5b6c13373fa"),
        }
        protocol_sha = hashlib.sha256(Path(validate_labels.__file__).read_bytes()).hexdigest()
        for language, (rows, dataset_sha, receipt_sha) in expected.items():
            with self.subTest(language=language):
                prefix = root / f"eval/bench/codocbench-{language}-v2"
                manifest_path = Path(f"{prefix}-screened-dev-manifest.json")
                parent_path = Path(f"{prefix}-dev-eligible-manifest.json")
                receipt_path = Path(f"{prefix}-selection-receipt.json")
                receipt_payload = receipt_path.read_bytes()
                receipt = json.loads(receipt_payload)
                self.assertEqual(hashlib.sha256(receipt_payload).hexdigest(), receipt_sha)
                self.assertEqual(receipt["rows"], rows)
                self.assertEqual(receipt["split"], "dev")
                self.assertEqual(receipt["selection_protocol"], "three-model-majority-v1")
                self.assertEqual(receipt["output_dataset_sha256"], dataset_sha)
                self.assertEqual(
                    hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
                    receipt["output_manifest_sha256"],
                )
                self.assertEqual(
                    hashlib.sha256(parent_path.read_bytes()).hexdigest(),
                    receipt["parent_manifest_sha256"],
                )
                self.assertEqual(receipt["screen_binding"]["dataset_sha256"],
                                 receipt["parent_dataset_sha256"])
                self.assertEqual(receipt["screen_binding"]["screen_protocol_sha256"],
                                 protocol_sha)
                self.assertEqual(receipt["screen_binding"]["annotators"],
                                 validate_labels.ANNOTATORS)
                assignments = load_split_assignments(manifest_path)
                self.assertEqual(len(assignments), rows)
                self.assertEqual(set(assignments.values()), {"dev"})
                manifest = json.loads(manifest_path.read_text())
                self.assertEqual(
                    {item["sha256"] for item in manifest["datasets"]}, {dataset_sha}
                )


if __name__ == "__main__":
    unittest.main()
