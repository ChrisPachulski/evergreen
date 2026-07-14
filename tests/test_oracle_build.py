import hashlib
import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("python", "java", "typescript", "rust", "go")


class OracleBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_frozen_similarity_policy_and_tokenizers_cover_five_languages(self):
        from eval.oracle.split import code_tokens, documentation_tokens, load_similarity_policy

        policy, digest = load_similarity_policy()
        self.assertEqual(policy["schema_version"], 1)
        self.assertEqual(policy["fuzzy"], {
            "metric": "set-jaccard-token-shingles",
            "shingle_tokens": 5,
            "threshold": 0.85,
            "minimum_tokens_each": 20,
        })
        self.assertEqual(digest, hashlib.sha256(
            (ROOT / "eval/oracle/similarity-policy-v1.json").read_bytes()
        ).hexdigest())

        samples = {
            "python": 'r"raw # text" # dropped\nvalue += 0x10\n',
            "java": 'String s = "/* text */"; /* dropped */ value >>= 2;',
            "typescript": 'const s = `raw // text`; // dropped\nvalue ??= 3;',
            "rust": 'let s = r##"raw /* text */"##; /* outer /* inner */ done */ value <<= 4;',
            "go": 's := `raw // text`; /* dropped */ value &^= 5',
        }
        operators = {
            "python": "+=", "java": ">>=", "typescript": "??=", "rust": "<<=", "go": "&^=",
        }
        for language, source in samples.items():
            with self.subTest(language=language):
                normalized = code_tokens(language, source, structural=False)
                structural = code_tokens(language, source, structural=True)
                self.assertIn("<str>", normalized)
                self.assertIn("<num>", normalized)
                self.assertIn(operators[language], normalized)
                self.assertNotIn("dropped", normalized)
                self.assertIn("<id>", structural)

        self.assertEqual(
            documentation_tokens("Returns 12 CAFÉ values; Version2 stays."),
            ("returns", "<num>", "caf", "values", "version", "<num>", "stays"),
        )

    def test_tokenizer_fails_closed_on_unknown_language_or_unterminated_construct(self):
        from eval.oracle.split import SimilarityError, code_tokens

        for language, source in (
            ("ruby", "puts 1"),
            ("python", 'value = "unterminated'),
            ("java", "/* unterminated"),
            ("typescript", "const x = `unterminated"),
            ("rust", 'let x = r##"unterminated"#;'),
            ("go", "x := `unterminated"),
        ):
            with self.subTest(language=language), self.assertRaises(SimilarityError):
                code_tokens(language, source)

    def test_similarity_is_field_separated_and_uses_declared_fuzzy_boundary(self):
        from eval.oracle.split import fuzzy_token_overlap, rows_overlap

        tokens = tuple(f"token-{index}" for index in range(100))
        one_change = (*tokens[:50], "changed", *tokens[51:])
        many_changes = (*tokens[:20], *(f"changed-{index}" for index in range(20)), *tokens[40:])
        self.assertTrue(fuzzy_token_overlap(tokens, one_change))
        self.assertFalse(fuzzy_token_overlap(tokens, many_changes))
        self.assertFalse(fuzzy_token_overlap(tokens[:19], tokens[:19]))

        code_a = " ".join(f"name{i}" for i in range(20))
        code_b_tokens = code_a.split()
        code_b_tokens[-1] = "changed"
        code_b = " ".join(code_b_tokens)
        base = {"language": "python", "code": code_a, "documentation": "short docs"}
        near = {"language": "python", "code": code_b, "documentation": "different docs"}
        self.assertTrue(rows_overlap(base, near))

        # Concatenating fields would collide; independent scans must not.
        left = {"language": "python", "code": "alpha beta", "documentation": "gamma delta"}
        right = {"language": "python", "code": "alpha", "documentation": "beta gamma delta"}
        self.assertFalse(rows_overlap(left, right))

    def test_keyed_lineage_split_keeps_projects_seeds_and_derivatives_together(self):
        from eval.oracle.split import assign_split

        key = b"k" * 32
        first = assign_split(key, "upstream-family")
        self.assertIn(first, ("dev", "holdout"))
        self.assertEqual(first, assign_split(key, "upstream-family"))
        self.assertNotEqual(
            {assign_split(key, f"family-{index}") for index in range(64)}, set()
        )

    def test_cross_split_lineage_project_and_similarity_leakage_fail(self):
        from eval.oracle.split import SimilarityError, validate_split_isolation

        rows = [
            {"id": "a", "project": "org/a", "lineage_id": "family-a", "split": "dev",
             "language": "python", "code": "def f(): return 1", "documentation": "returns one"},
            {"id": "b", "project": "org/b", "lineage_id": "family-b", "split": "holdout",
             "language": "python", "code": "def f(): return 1", "documentation": "returns one"},
        ]
        with self.assertRaisesRegex(SimilarityError, "overlap"):
            validate_split_isolation(rows, [])

        rows[1]["code"] = "def g(): return 2"
        rows[1]["documentation"] = "returns two"
        rows[1]["project"] = "org/a"
        with self.assertRaisesRegex(SimilarityError, "project"):
            validate_split_isolation(rows, [])

        rows[1]["project"] = "org/b"
        rows[1]["lineage_id"] = "family-a"
        with self.assertRaisesRegex(SimilarityError, "lineage"):
            validate_split_isolation(rows, [])

    def test_missing_lineage_and_overlap_with_reference_corpora_fail_admission(self):
        from eval.oracle.split import SimilarityError, validate_split_isolation

        row = {"id": "a", "project": "org/a", "lineage_id": "", "split": "dev",
               "language": "python", "code": "return 1", "documentation": "returns one"}
        with self.assertRaisesRegex(SimilarityError, "lineage"):
            validate_split_isolation([row], [])

        row["lineage_id"] = "family-a"
        reference = {"category": "test", "source": "tests/fixture.py", "field": "code",
                     "language": "python", "text": "return 1"}
        with self.assertRaisesRegex(SimilarityError, "reference"):
            validate_split_isolation([row], [reference])

    def package_rows(self, *, languages=LANGUAGES, repositories=10, each_class=2):
        rows = []
        for language in languages:
            for repository in range(repositories):
                for label in ("consistent", "inconsistent"):
                    for index in range(each_class):
                        rows.append({
                            "id": f"{language}-{repository}-{label}-{index}",
                            "project": f"org/{language}-{repository}",
                            "lineage_id": f"family-{language}-{repository}",
                            "language": language,
                            "label": label,
                            "oracle_kind": "return-value",
                        })
        return rows

    def test_package_constraints_reject_minimums_imbalance_duplicates_and_share(self):
        from eval.oracle.build import PackageError, PackageLimits, validate_package_rows

        limits = PackageLimits(
            minimum_per_class=2, minimum_repositories=2, maximum_class_ratio=2.0,
            maximum_repository_share=0.5, minimum_kind_inconsistent=1,
            minimum_kind_consistent=1,
        )
        valid = self.package_rows(languages=("python",), repositories=2, each_class=2)
        validate_package_rows(
            valid, languages=("python",), limits=limits, oracle_kinds=("return-value",),
        )

        cases = []
        below_minimum = [row for row in valid if row["label"] == "consistent"] + [
            next(row for row in valid if row["label"] == "inconsistent")
        ]
        cases.append((below_minimum, "minimum"))
        cases.append((valid + [dict(valid[0])], "duplicate"))
        imbalanced = valid + [
            {**valid[0], "id": f"extra-{index}", "project": f"org/extra-{index}"}
            for index in range(5)
        ]
        cases.append((imbalanced, "imbalance"))
        concentrated = [dict(row) for row in valid]
        for row in concentrated[:5]:
            row["project"] = "org/dominant"
            row["lineage_id"] = "family-dominant"
        cases.append((concentrated, "repository share"))
        invalid_label = valid + [{
            **valid[0], "id": "invalid-label", "project": "org/invalid", "label": "unknown",
        }]
        cases.append((invalid_label, "label"))
        for rows, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(PackageError, message):
                validate_package_rows(
                    rows, languages=("python",), limits=limits,
                    oracle_kinds=("return-value",),
                )

    def test_package_constraints_require_every_language_kind_class_cell(self):
        from eval.oracle.build import PackageError, PackageLimits, validate_package_rows
        from eval.oracle.oracle import ORACLE_KINDS

        defaults = PackageLimits()
        self.assertEqual(defaults.minimum_kind_inconsistent, 20)
        self.assertEqual(defaults.minimum_kind_consistent, 40)
        limits = PackageLimits(
            minimum_per_class=1, minimum_repositories=1, maximum_class_ratio=3.0,
            maximum_repository_share=1.0, minimum_kind_inconsistent=2,
            minimum_kind_consistent=4,
        )
        rows = []
        for kind in ORACLE_KINDS:
            for label, count in (("inconsistent", 2), ("consistent", 4)):
                for index in range(count):
                    rows.append({
                        "id": f"{kind}-{label}-{index}", "project": f"org/{kind}",
                        "lineage_id": f"family-{kind}", "language": "python",
                        "oracle_kind": kind, "label": label,
                    })
        validate_package_rows(rows, languages=("python",), limits=limits)

        for mutation, message in (
            ([row for row in rows if row["oracle_kind"] != "raises"], "kind cell"),
            ([row for row in rows if not (
                row["oracle_kind"] == "raises" and row["label"] == "inconsistent" and
                row["id"].endswith("-1")
            )], "kind cell"),
            ([{**row, "oracle_kind": "nominal-placeholder"} if row is rows[0] else row
              for row in rows], "oracle kind"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(PackageError, message):
                validate_package_rows(mutation, languages=("python",), limits=limits)

    def test_source_group_split_preflight_uses_only_public_groups_before_derivation(self):
        from eval.oracle.build import (
            PackageError, PackageLimits, SourceGroupLimits, _build_packages,
        )
        from eval.oracle.split import assign_split

        key = b"public-group-count-key" * 2
        self.assertEqual(SourceGroupLimits(), SourceGroupLimits(20, 10))
        by_split = {"dev": [], "holdout": []}
        index = 0
        while min(map(len, by_split.values())) < 11:
            lineage = f"family-{index}"
            by_split[assign_split(key, lineage)].append(
                self.seed_entry(f"org/project-{index}", lineage, f"seed-{index}")
            )
            index += 1

        derive = mock.Mock(side_effect=AssertionError("private derivation must not start"))
        with self.assertRaisesRegex(PackageError, "20 repository groups per language"):
            _build_packages(
                self.write_manifest(by_split["dev"][:5] + by_split["holdout"][:5]),
                self.root / "private-ten-total", self.root / "public-ten-total.json", key, {},
                references=self.references(), derive_seed=derive,
                limits=PackageLimits(1, 1, 2.0, 1.0, 1, 1), languages=("python",),
                source_group_limits=SourceGroupLimits(20, 10),
                oracle_kinds=("return-value",),
            )
        derive.assert_not_called()

        with self.assertRaisesRegex(PackageError, "10 repository groups in each split"):
            _build_packages(
                self.write_manifest(by_split["dev"][:11] + by_split["holdout"][:9]),
                self.root / "private-preflight", self.root / "public-preflight.json", key, {},
                references=self.references(), derive_seed=derive,
                limits=PackageLimits(1, 1, 2.0, 1.0, 1, 1), languages=("python",),
                source_group_limits=SourceGroupLimits(20, 10),
                oracle_kinds=("return-value",),
            )
        derive.assert_not_called()

        duplicate_lineage = [
            self.seed_entry(f"org/fork-{item}", "one-family", f"fork-seed-{item}")
            for item in range(20)
        ]
        with self.assertRaisesRegex(PackageError, "20 repository groups per language"):
            _build_packages(
                self.write_manifest(duplicate_lineage), self.root / "private-forks",
                self.root / "public-forks.json", key, {}, references=self.references(),
                derive_seed=derive, limits=PackageLimits(1, 1, 2.0, 1.0, 1, 1),
                languages=("python",), source_group_limits=SourceGroupLimits(20, 10),
                oracle_kinds=("return-value",),
            )
        derive.assert_not_called()

    def seed_entry(self, project, lineage_id, group_id):
        return {
            "lineage_id": lineage_id,
            "seed": {"project": project, "group_id": group_id, "language": "python",
                     "seed_sha256": hashlib.sha256(group_id.encode()).hexdigest()},
        }

    @staticmethod
    def derive(seed, approved_images):
        del approved_images
        base = {
            "group_id": seed["group_id"], "project": seed["project"],
            "language": seed["language"], "documentation": f"docs {seed['group_id']}",
            "documentation_sha256": "d" * 64, "oracle_kind": "return-value",
            "seed_sha256": seed["seed_sha256"],
        }
        source = (f"def {seed['group_id'].replace('-', '_')}(): return {{value}}"
                  if seed["group_id"].endswith("a") else
                  f"{seed['group_id'].replace('-', '_')} = [{{value}}]")
        return tuple({
            **base, "id": f"{seed['group_id']}:{variant}", "variant": variant,
            "code": source.format(value=value),
            "code_sha256": hashlib.sha256(f"{variant}-{seed['group_id']}".encode()).hexdigest(),
            "mutation_id": mutation, "label": label,
        } for variant, value, mutation, label in (
            ("source", 1, None, "consistent"),
            ("mutation", 2, "integer-literal-1-to-2-v1", "inconsistent"),
            ("semantic-noop", 1, "comment-v1", "consistent"),
        ))

    def write_manifest(self, entries):
        from eval.oracle.split import load_similarity_policy

        _policy, policy_hash = load_similarity_policy()
        path = self.root / "sources.json"
        path.write_text(json.dumps({
            "schema_version": 1, "similarity_policy_sha256": policy_hash, "seeds": entries,
        }))
        return path

    @staticmethod
    def references():
        return [{
            "category": category, "source": f"{category}/unrelated.txt",
            "field": "documentation", "language": "python",
            "text": f"entirely unrelated {category} exclusion material",
        } for category in ("prompt", "example", "test", "fixture", "prior-corpus")]

    def test_build_is_order_independent_private_and_public_outputs_are_bound_and_nonleaking(self):
        from eval.oracle.build import PackageLimits, SourceGroupLimits, _build_packages

        entries = [
            self.seed_entry("org/a", "family-0", "seed-a"),
            self.seed_entry("org/b", "family-3", "seed-b"),
        ]
        limits = PackageLimits(1, 1, 2.0, 1.0, 1, 2)
        outputs = []
        for index, ordered in enumerate((entries, list(reversed(entries)))):
            private = self.root / f"private-{index}"
            public = self.root / f"public-{index}.json"
            result = _build_packages(
                self.write_manifest(ordered), private, public, b"split-key" * 4, {},
                references=self.references(), derive_seed=self.derive, limits=limits,
                languages=("python",), source_group_limits=SourceGroupLimits(2, 1),
                oracle_kinds=("return-value",),
            )
            outputs.append((result, private, public))

        first, second = outputs
        self.assertEqual(first[0][0]["package_sha256"], second[0][0]["package_sha256"])
        self.assertEqual(first[0][1]["package_sha256"], second[0][1]["package_sha256"])
        self.assertEqual(first[2].read_bytes(), second[2].read_bytes())
        for result, private, public in outputs:
            self.assertEqual(stat.S_IMODE(private.stat().st_mode), 0o700)
            for split in ("dev", "holdout"):
                self.assertEqual(stat.S_IMODE((private / f"{split}.jsonl").stat().st_mode), 0o600)
            public_text = public.read_text()
            for forbidden in ("org/a", "org/b", "docs seed", "consistent", "mutation_id",
                              "split-key", "lineage_id", "observable", '"code"'):
                self.assertNotIn(forbidden, public_text)
            document = json.loads(public_text)
            self.assertEqual(document["similarity_policy_sha256"], result[0]["policy_sha256"])
            self.assertTrue(all(set(row) == {"dataset_sha256", "id", "split"}
                                for row in document["rows"]))

    def test_existing_private_destination_fails_exclusive_creation(self):
        from eval.oracle.build import PackageError, PackageLimits, SourceGroupLimits, _build_packages

        private = self.root / "private"
        private.mkdir(mode=0o700)
        with self.assertRaisesRegex(PackageError, "private destination"):
            _build_packages(
                self.write_manifest([self.seed_entry("org/a", "a", "seed-a")]),
                private, self.root / "public.json", b"k" * 32, {},
                references=self.references(), derive_seed=self.derive,
                limits=PackageLimits(1, 1, 2.0, 1.0, 1, 2), languages=("python",),
                source_group_limits=SourceGroupLimits(2, 1),
                oracle_kinds=("return-value",),
            )

    def test_build_requires_exclusion_corpus_and_external_private_destination(self):
        from eval.oracle.build import PackageError, PackageLimits, _build_packages

        manifest = self.write_manifest([self.seed_entry("org/a", "family-a", "seed-a")])
        common = {
            "derive_seed": self.derive,
            "limits": PackageLimits(1, 1, 2.0, 1.0, 1, 2),
            "languages": ("python",),
        }
        with self.assertRaisesRegex(PackageError, "reference corpus"):
            _build_packages(manifest, self.root / "private-none", self.root / "public-none.json",
                            b"k" * 32, {}, **common)
        with self.assertRaisesRegex(PackageError, "complete reference corpus"):
            _build_packages(
                self.write_manifest([
                    self.seed_entry("org/a", "family-0", "seed-a"),
                    self.seed_entry("org/b", "family-3", "seed-b"),
                ]), self.root / "private-incomplete", self.root / "public-incomplete.json",
                b"split-key" * 4, {}, references=self.references()[:-1], **common,
            )
        with self.assertRaisesRegex(PackageError, "outside the repository"):
            _build_packages(manifest, ROOT / ".private-forbidden", self.root / "public.json",
                            b"k" * 32, {}, references=self.references(), **common)

    def test_public_builder_cannot_replace_or_weaken_oracle_and_package_gates(self):
        from eval.oracle.build import build_packages

        parameters = inspect.signature(build_packages).parameters
        self.assertNotIn("derive_seed", parameters)
        self.assertNotIn("limits", parameters)
        self.assertNotIn("languages", parameters)
        self.assertNotIn("source_group_limits", parameters)
        self.assertNotIn("oracle_kinds", parameters)

    def test_failed_public_write_removes_exclusively_created_private_package(self):
        from eval.oracle.build import (
            PackageError, PackageLimits, SourceGroupLimits, _build_packages,
        )

        blocker = self.root / "not-a-directory"
        blocker.write_text("blocked")
        private = self.root / "private-cleanup"
        with self.assertRaisesRegex(PackageError, "public split manifest"):
            _build_packages(
                self.write_manifest([
                    self.seed_entry("org/a", "family-0", "seed-a"),
                    self.seed_entry("org/b", "family-3", "seed-b"),
                ]), private, blocker / "public.json", b"split-key" * 4, {},
                references=self.references(), derive_seed=self.derive,
                limits=PackageLimits(1, 1, 2.0, 1.0, 1, 2), languages=("python",),
                source_group_limits=SourceGroupLimits(2, 1),
                oracle_kinds=("return-value",),
            )
        self.assertFalse(private.exists())

    def test_development_loader_never_opens_holdout_package(self):
        from eval.oracle.build import (
            PackageLimits, SourceGroupLimits, _build_packages, load_development_rows,
        )

        private = self.root / "private"
        public = self.root / "public.json"
        _build_packages(
            self.write_manifest([
                self.seed_entry("org/a", "family-0", "seed-a"),
                self.seed_entry("org/b", "family-1", "seed-b"),
            ]), private, public, b"key" * 16, {}, derive_seed=self.derive,
            references=self.references(), limits=PackageLimits(1, 1, 2.0, 1.0, 1, 2),
            languages=("python",), source_group_limits=SourceGroupLimits(2, 1),
            oracle_kinds=("return-value",),
        )
        holdout = (private / "holdout.jsonl").resolve()
        original = Path.read_bytes

        def guarded(path):
            if path.resolve() == holdout:
                raise AssertionError("holdout package was opened")
            return original(path)

        with mock.patch.object(Path, "read_bytes", guarded):
            rows = load_development_rows(public, private / "dev.jsonl")
        self.assertTrue(rows)
        self.assertTrue(all(row["split"] == "dev" for row in rows))

        document = json.loads(public.read_text())
        document["rows"][0]["project"] = "org/private"
        public.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "forbidden"):
            load_development_rows(public, private / "dev.jsonl")

    def test_development_cli_accepts_no_holdout_path_and_opens_only_dev(self):
        from eval.oracle.build import PackageLimits, SourceGroupLimits, _build_packages

        private = self.root / "private-cli"
        public = self.root / "public-cli.json"
        _build_packages(
            self.write_manifest([
                self.seed_entry("org/a", "family-0", "seed-a"),
                self.seed_entry("org/b", "family-1", "seed-b"),
            ]), private, public, b"key" * 16, {}, references=self.references(),
            derive_seed=self.derive, limits=PackageLimits(1, 1, 2.0, 1.0, 1, 2),
            languages=("python",), source_group_limits=SourceGroupLimits(2, 1),
            oracle_kinds=("return-value",),
        )
        holdout = private / "holdout.jsonl"
        holdout.chmod(0)
        completed = subprocess.run(
            [sys.executable, "-m", "eval.oracle.build", "development",
             "--manifest", str(public), "--package", str(private / "dev.jsonl")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        holdout.chmod(0o600)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        rows = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all(row["split"] == "dev" for row in rows))


if __name__ == "__main__":
    unittest.main()
