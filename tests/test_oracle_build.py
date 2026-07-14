import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("python", "java", "typescript", "rust", "go")


class OracleBuildTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()

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
            "python": 'r"raw # text" # dropped\nvalue += 0x10 \\\n+                + 1\n',
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
                            "variant": ("mutation" if label == "inconsistent" else
                                        "source" if index % 2 == 0 else "semantic-noop"),
                            "mutation_id": (
                                "return-value-1-to-2-v1" if label == "inconsistent" else
                                None if index % 2 == 0 else "comment-v1"
                            ),
                        })
        return rows

    @staticmethod
    def required_scale_rows():
        from eval.oracle.oracle import MUTATION_OPERATORS, ORACLE_KINDS

        operator_by_kind = {
            contract["kind"]: identity for identity, contract in MUTATION_OPERATORS.items()
        }
        code = {
            "python": {
                "dev": "def evergreen_development():\n    pass\n",
                "holdout": "while False:\n    break\n",
            },
            "java": {
                "dev": "class EvergreenDevelopment {}",
                "holdout": "interface EvergreenHoldout {}",
            },
            "typescript": {
                "dev": "const evergreenDevelopment = true;",
                "holdout": "let evergreenHoldout = false;",
            },
            "rust": {
                "dev": "fn evergreen_development() {}",
                "holdout": "const EVERGREEN_HOLDOUT: bool = false;",
            },
            "go": {
                "dev": "package main\nfunc evergreenDevelopment() {}",
                "holdout": "package main\nconst evergreenHoldout = false",
            },
        }
        rows = []
        for language in LANGUAGES:
            for kind in ORACLE_KINDS:
                for split_name in ("dev", "holdout"):
                    for index in range(75):
                        control = index % 3
                        variant = ("mutation" if control == 0 else
                                   "source" if control == 1 else "semantic-noop")
                        identity = f"required-{language}-{kind}-{split_name}-{index}"
                        rows.append({
                            "id": identity,
                            "project": f"org/{identity}",
                            "lineage_id": identity,
                            "split": split_name,
                            "language": language,
                            "oracle_kind": kind,
                            "variant": variant,
                            "mutation_id": (
                                operator_by_kind[kind] if variant == "mutation" else
                                None if variant == "source" else "comment-v1"
                            ),
                            "label": "inconsistent" if variant == "mutation" else "consistent",
                            "code": code[language][split_name],
                            "documentation": f"{language} {split_name} behavior",
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
                        "variant": ("mutation" if label == "inconsistent" else
                                    "source" if index % 2 == 0 else "semantic-noop"),
                        "mutation_id": (
                            {
                                "return-value": "return-value-1-to-2-v1",
                                "raises": "raises-none-to-value-error-v1",
                                "default-value": "default-value-one-to-two-v1",
                                "cardinality": "cardinality-one-to-two-v1",
                                "state-change": "state-change-before-to-after-v1",
                            }[kind] if label == "inconsistent" else
                            None if index % 2 == 0 else "comment-v1"
                        ),
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

        wrong_operator = [dict(row) for row in rows]
        target = next(row for row in wrong_operator if row["oracle_kind"] == "raises" and
                      row["label"] == "inconsistent")
        target["mutation_id"] = "return-value-1-to-2-v1"
        with self.assertRaisesRegex(PackageError, "operator contract"):
            validate_package_rows(wrong_operator, languages=("python",), limits=limits)

    def test_kind_cells_independently_require_repository_diversity_and_share(self):
        from eval.oracle.build import PackageError, PackageLimits, validate_package_rows
        from eval.oracle.oracle import ORACLE_KINDS

        limits = PackageLimits(
            minimum_per_class=1, minimum_repositories=2, maximum_class_ratio=3.0,
            maximum_repository_share=0.75, minimum_kind_inconsistent=1,
            minimum_kind_consistent=2,
        )
        rows = []
        for kind_index, kind in enumerate(ORACLE_KINDS):
            for label, count in (("inconsistent", 1), ("consistent", 2)):
                for index in range(count):
                    rows.append({
                        "id": f"{kind}-{label}-{index}", "project": f"org/{kind_index}",
                        "lineage_id": f"family-{kind_index}", "language": "python",
                        "oracle_kind": kind, "label": label,
                        "variant": ("mutation" if label == "inconsistent" else
                                    "source" if index % 2 == 0 else "semantic-noop"),
                        "mutation_id": (
                            {
                                "return-value": "return-value-1-to-2-v1",
                                "raises": "raises-none-to-value-error-v1",
                                "default-value": "default-value-one-to-two-v1",
                                "cardinality": "cardinality-one-to-two-v1",
                                "state-change": "state-change-before-to-after-v1",
                            }[kind] if label == "inconsistent" else
                            None if index % 2 == 0 else "comment-v1"
                        ),
                    })
        with self.assertRaisesRegex(PackageError, "kind cell repository minimum"):
            validate_package_rows(rows, languages=("python",), limits=limits)

    def test_nonfinite_package_limits_and_noncanonical_json_fail_closed(self):
        from eval.oracle.build import PackageError, PackageLimits, _load_json

        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(PackageError):
                PackageLimits(maximum_class_ratio=value)
            with self.subTest(share=value), self.assertRaises(PackageError):
                PackageLimits(maximum_repository_share=value)
        for name, raw in (("duplicate", '{"a":1,"a":2}'), ("nan", '{"a":NaN}')):
            path = self.root / f"{name}.json"
            path.write_text(raw)
            with self.subTest(name=name), self.assertRaisesRegex(PackageError, "invalid"):
                _load_json(path, "test JSON")

    def test_similarity_bounds_fail_before_profile_or_quadratic_work(self):
        from eval.oracle import split

        row = {"id": "x", "project": "org/x", "lineage_id": "x", "split": "dev",
               "language": "python", "code": "return 1", "documentation": "returns one"}
        with mock.patch.object(split, "_profile", side_effect=AssertionError("profiled")):
            with self.assertRaisesRegex(split.SimilarityError, "row limit"):
                split.validate_split_isolation([row] * (split.MAX_SIMILARITY_ROWS + 1), [])
        with self.assertRaisesRegex(split.SimilarityError, "text byte limit"):
            split.code_tokens("python", "x" * (split.MAX_TEXT_BYTES + 1))
        with mock.patch.object(split.time, "monotonic", side_effect=[0.0, 31.0]), \
                mock.patch.object(split, "_profile", side_effect=AssertionError("profiled")):
            with self.assertRaisesRegex(split.SimilarityError, "deadline"):
                split.validate_split_isolation([row], [])

    def test_required_3750_row_corpus_fits_frozen_reference_comparison_cap(self):
        from eval.oracle import split
        from eval.oracle.build import _load_reference_inventory, validate_package_rows

        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True,
        ).strip()
        references, _digest = _load_reference_inventory(commit, tree, require_clean=False)
        self.assertEqual(len(references), 79)
        required_comparisons = 1875 * 1875 + 3750 * len(references)
        self.assertEqual(required_comparisons, 3_811_875)
        self.assertEqual(split.MAX_COMPARISONS, 5_000_000)
        self.assertLessEqual(required_comparisons, split.MAX_COMPARISONS)
        rows = self.required_scale_rows()
        self.assertEqual(len(rows), 3750)
        self.assertEqual({row["language"] for row in rows}, set(LANGUAGES))
        self.assertEqual(len({row["oracle_kind"] for row in rows}), 5)
        for split_name in ("dev", "holdout"):
            package = [row for row in rows if row["split"] == split_name]
            self.assertEqual(len(package), 1875)
            self.assertTrue(validate_package_rows(package))

        started = time.monotonic()
        self.assertTrue(split.validate_split_isolation(rows, references))
        self.assertLess(
            time.monotonic() - started, split.MAX_SIMILARITY_SECONDS,
            "real production-scale similarity work must fit the declared deadline",
        )

    def test_private_jsonl_rejects_duplicate_keys_and_nonfinite_numbers(self):
        from eval.oracle.build import PackageError, _read_private_rows

        for name, raw in (
            ("duplicate", b'{"id":"a","id":"b"}\n'),
            ("nonfinite", b'{"id":"a","score":NaN}\n'),
        ):
            path = self.root / f"strict-{name}.jsonl"
            path.write_bytes(raw)
            path.chmod(0o600)
            with self.subTest(name=name), self.assertRaisesRegex(PackageError, "invalid"):
                _read_private_rows(path)

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
                self.root / "dev-ten-total", self.root / "hold-ten-total",
                self.root / "public-ten-total.json", key, {},
                references=self.references(), derive_seed=derive,
                limits=PackageLimits(1, 1, 2.0, 1.0, 1, 1), languages=("python",),
                source_group_limits=SourceGroupLimits(20, 10),
                oracle_kinds=("return-value",),
            )
        derive.assert_not_called()

        with self.assertRaisesRegex(PackageError, "10 repository groups in each split"):
            _build_packages(
                self.write_manifest(by_split["dev"][:11] + by_split["holdout"][:9]),
                self.root / "dev-preflight", self.root / "hold-preflight",
                self.root / "public-preflight.json", key, {},
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
                self.write_manifest(duplicate_lineage), self.root / "dev-forks",
                self.root / "hold-forks", self.root / "public-forks.json", key, {},
                references=self.references(),
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
            ("mutation", 2, "return-value-1-to-2-v1", "inconsistent"),
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

    def write_reference_inventory(self):
        entries = []
        for reference in self.references():
            path = self.root / reference["source"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(reference["text"])
            entries.append({
                "category": reference["category"], "path": str(path),
                "field": reference["field"], "language": reference["language"],
                "sha256": hashlib.sha256(reference["text"].encode()).hexdigest(),
            })
        inventory = self.root / "reference-inventory.json"
        raw = json.dumps({"schema_version": 1, "entries": entries}, sort_keys=True).encode()
        inventory.write_bytes(raw)
        return inventory, hashlib.sha256(raw).hexdigest()

    def test_reference_corpus_is_derived_from_hash_bound_actual_files(self):
        from eval.oracle.build import PackageError, _load_reference_inventory

        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
        tree = subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=ROOT, text=True,
        ).strip()
        references, digest = _load_reference_inventory(commit, tree, require_clean=False)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertEqual({item["category"] for item in references}, {
            "prompt", "example", "test", "fixture", "prior-corpus",
        })
        sources = {item["source"] for item in references}
        for required in (
            "eval/prompt.md", "commands/cultivate.md", "examples/_index.md",
            "tests/test_cli.py", "eval/fixture/cli.py", "eval/bench/dataset.jsonl",
        ):
            self.assertIn(required, sources)
        fields = {item["source"]: item["field"] for item in references}
        self.assertEqual(fields["tests/test_cli.py"], "code")
        self.assertEqual(fields["eval/fixture/cli.py"], "code")
        inventory, digest = self.write_reference_inventory()
        with self.assertRaisesRegex(PackageError, "caller-supplied"):
            _load_reference_inventory(
                commit, tree, require_clean=False, caller_inventory=(inventory, digest),
            )

        dirty = ROOT / ".reference-inventory-dirty-test"
        dirty.write_text("untracked")
        try:
            with self.assertRaisesRegex(PackageError, "dirty"):
                _load_reference_inventory(commit, tree)
        finally:
            dirty.unlink()

    def test_source_manifest_must_be_external_to_detector_repository(self):
        from eval.oracle.build import PackageError, _manifest
        from eval.oracle.split import load_similarity_policy

        _policy, policy_hash = load_similarity_policy()
        external = self.write_manifest([self.seed_entry("org/a", "family-a", "seed-a")])
        with tempfile.NamedTemporaryFile(dir=ROOT, suffix=".json", delete=False) as output:
            inside = Path(output.name)
            output.write(external.read_bytes())
        try:
            with self.assertRaisesRegex(PackageError, "outside the detector repository"):
                _manifest(inside, policy_hash)
        finally:
            inside.unlink()

    def test_public_builder_requires_separate_dev_holdout_roots_and_inventory(self):
        from eval.oracle.build import build_packages

        parameters = inspect.signature(build_packages).parameters
        self.assertIn("development_root", parameters)
        self.assertIn("holdout_root", parameters)
        self.assertNotIn("reference_inventory", parameters)
        self.assertNotIn("reference_inventory_sha256", parameters)
        self.assertNotIn("private_directory", parameters)
        self.assertIn("subject_commit", parameters)
        self.assertIn("subject_tree", parameters)
        self.assertNotIn("references", parameters)

    def test_development_path_identity_is_authenticated_before_package_open(self):
        from eval.oracle.build import PackageError, load_development_rows
        from eval.oracle.split import POLICY_SHA256

        development = self.root / "dev-root" / "oracle.jsonl"
        holdout = self.root / "holdout-root" / "oracle.jsonl"
        dev_id = "oracle-" + "a" * 64
        holdout_id = "oracle-" + "b" * 64
        document = {
            "schema_version": 2, "similarity_policy_sha256": POLICY_SHA256,
            "reference_corpus_sha256": "e" * 64,
            "subject_commit": "a" * 40, "subject_tree": "b" * 40,
            "datasets": [
                {"sha256": "c" * 64, "path_sha256": hashlib.sha256(
                    os.fsencode(str(development.absolute()))
                ).hexdigest(), "split": "dev", "rows": 1},
                {"sha256": "d" * 64, "path_sha256": hashlib.sha256(
                    os.fsencode(str(holdout.absolute()))
                ).hexdigest(), "split": "holdout", "rows": 1},
            ],
            "rows": [
                {"id": dev_id, "dataset_sha256": "c" * 64, "split": "dev"},
                {"id": holdout_id, "dataset_sha256": "d" * 64, "split": "holdout"},
            ],
        }
        manifest = self.root / "public.json"
        manifest.write_text(json.dumps(document))
        with mock.patch(
            "eval.oracle.build._read_private_rows", side_effect=AssertionError("opened package")
        ) as opened, self.assertRaisesRegex(PackageError, "path identity"):
            load_development_rows(manifest, holdout)
        opened.assert_not_called()

    def test_development_loader_rejects_duplicate_public_manifest_keys(self):
        from eval.oracle.build import PackageError, load_development_rows

        manifest = self.root / "duplicate-public.json"
        manifest.write_text('{"schema_version":2,"schema_version":2}')
        with self.assertRaisesRegex(PackageError, "invalid"):
            load_development_rows(manifest, self.root / "never-opened.jsonl")

    def test_private_roots_are_distinct_and_symlink_free_before_derivation(self):
        from eval.oracle.build import PackageError, PackageLimits, SourceGroupLimits, _build_packages

        entries = [
            self.seed_entry("org/a", "family-0", "seed-a"),
            self.seed_entry("org/b", "family-3", "seed-b"),
        ]
        common = {
            "references": self.references(), "derive_seed": mock.Mock(
                side_effect=AssertionError("derivation started")
            ), "limits": PackageLimits(1, 1, 2.0, 1.0, 1, 2),
            "languages": ("python",), "source_group_limits": SourceGroupLimits(2, 1),
            "oracle_kinds": ("return-value",),
        }
        manifest = self.write_manifest(entries)
        with self.assertRaisesRegex(PackageError, "separate"):
            _build_packages(
                manifest, self.root / "same", self.root / "same", self.root / "public.json",
                b"split-key" * 4, {}, **common,
            )
        real = self.root / "real-parent"
        real.mkdir()
        alias = self.root / "linked-parent"
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(PackageError, "symlink"):
            _build_packages(
                manifest, alias / "dev", self.root / "holdout", self.root / "public.json",
                b"split-key" * 4, {}, **common,
            )
        common["derive_seed"].assert_not_called()

    def test_build_is_order_independent_private_and_public_outputs_are_bound_and_nonleaking(self):
        from eval.oracle.build import PackageLimits, SourceGroupLimits, _build_packages

        entries = [
            self.seed_entry("org/a", "family-0", "seed-a"),
            self.seed_entry("org/b", "family-3", "seed-b"),
        ]
        limits = PackageLimits(1, 1, 2.0, 1.0, 1, 2)
        outputs = []
        for index, ordered in enumerate((entries, list(reversed(entries)))):
            development = self.root / f"development-{index}"
            holdout = self.root / f"holdout-{index}"
            public = self.root / f"public-{index}.json"
            result = _build_packages(
                self.write_manifest(ordered), development, holdout, public, b"split-key" * 4, {},
                references=self.references(), derive_seed=self.derive, limits=limits,
                languages=("python",), source_group_limits=SourceGroupLimits(2, 1),
                oracle_kinds=("return-value",),
            )
            outputs.append((result, development, holdout, public))

        first, second = outputs
        self.assertEqual(first[0][0]["package_sha256"], second[0][0]["package_sha256"])
        self.assertEqual(first[0][1]["package_sha256"], second[0][1]["package_sha256"])
        public_documents = [json.loads(item[3].read_text()) for item in outputs]
        for document in public_documents:
            for declaration in document["datasets"]:
                declaration.pop("path_sha256")
        self.assertEqual(public_documents[0], public_documents[1])
        for result, development, holdout, public in outputs:
            for root in (development, holdout):
                self.assertEqual(stat.S_IMODE(root.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((root / "oracle.jsonl").stat().st_mode), 0o600)
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
                private, self.root / "holdout", self.root / "public.json", b"k" * 32, {},
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
            _build_packages(manifest, self.root / "dev-none", self.root / "hold-none",
                            self.root / "public-none.json", b"k" * 32, {}, **common)
        with self.assertRaisesRegex(PackageError, "complete reference corpus"):
            _build_packages(
                self.write_manifest([
                    self.seed_entry("org/a", "family-0", "seed-a"),
                    self.seed_entry("org/b", "family-3", "seed-b"),
                ]), self.root / "dev-incomplete", self.root / "hold-incomplete",
                self.root / "public-incomplete.json",
                b"split-key" * 4, {}, references=self.references()[:-1], **common,
            )
        with self.assertRaisesRegex(PackageError, "outside the repository"):
            _build_packages(manifest, ROOT / ".private-forbidden", self.root / "holdout",
                            self.root / "public.json", b"k" * 32, {},
                            references=self.references(), **common)

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
        development = self.root / "development-cleanup"
        holdout = self.root / "holdout-cleanup"
        with self.assertRaisesRegex(PackageError, "public split manifest"):
            _build_packages(
                self.write_manifest([
                    self.seed_entry("org/a", "family-0", "seed-a"),
                    self.seed_entry("org/b", "family-3", "seed-b"),
                ]), development, holdout, blocker / "public.json", b"split-key" * 4, {},
                references=self.references(), derive_seed=self.derive,
                limits=PackageLimits(1, 1, 2.0, 1.0, 1, 2), languages=("python",),
                source_group_limits=SourceGroupLimits(2, 1),
                oracle_kinds=("return-value",),
            )
        self.assertFalse(development.exists())
        self.assertFalse(holdout.exists())

    def test_private_root_swap_never_deletes_attacker_replacement(self):
        from eval.oracle import build
        from eval.oracle.build import PackageError, PackageLimits, SourceGroupLimits

        development = self.root / "development-swap"
        displaced = self.root / "development-displaced"
        holdout = self.root / "holdout-swap"
        public = self.root / "public-swap.json"
        original = build._atomic_public

        def swap_then_publish(parent_descriptor, name, raw):
            development.rename(displaced)
            development.mkdir()
            (development / "attacker.txt").write_text("do not delete")
            return original(parent_descriptor, name, raw)

        with mock.patch.object(build, "_atomic_public", side_effect=swap_then_publish), \
                self.assertRaisesRegex(PackageError, "changed during publication"):
            build._build_packages(
                self.write_manifest([
                    self.seed_entry("org/a", "family-0", "seed-a"),
                    self.seed_entry("org/b", "family-3", "seed-b"),
                ]), development, holdout, public, b"split-key" * 4, {},
                references=self.references(), derive_seed=self.derive,
                limits=PackageLimits(1, 1, 2.0, 1.0, 1, 2), languages=("python",),
                source_group_limits=SourceGroupLimits(2, 1),
                oracle_kinds=("return-value",),
            )
        self.assertEqual((development / "attacker.txt").read_text(), "do not delete")
        self.assertTrue(displaced.is_dir())
        self.assertEqual(list(displaced.iterdir()), [])
        self.assertFalse(holdout.exists())
        self.assertFalse(public.exists())

    def test_development_loader_never_opens_holdout_package(self):
        from eval.oracle.build import (
            PackageLimits, SourceGroupLimits, _build_packages, load_development_rows,
        )

        development = self.root / "development"
        holdout_root = self.root / "holdout"
        public = self.root / "public.json"
        _build_packages(
            self.write_manifest([
                self.seed_entry("org/a", "family-0", "seed-a"),
                self.seed_entry("org/b", "family-1", "seed-b"),
            ]), development, holdout_root, public, b"key" * 16, {}, derive_seed=self.derive,
            references=self.references(), limits=PackageLimits(1, 1, 2.0, 1.0, 1, 2),
            languages=("python",), source_group_limits=SourceGroupLimits(2, 1),
            oracle_kinds=("return-value",),
        )
        holdout = (holdout_root / "oracle.jsonl").resolve()
        original = Path.read_bytes

        def guarded(path):
            if path.resolve() == holdout:
                raise AssertionError("holdout package was opened")
            return original(path)

        with mock.patch.object(Path, "read_bytes", guarded):
            rows = load_development_rows(public, development / "oracle.jsonl")
        self.assertTrue(rows)
        self.assertTrue(all(row["split"] == "dev" for row in rows))

        document = json.loads(public.read_text())
        document["rows"][0]["project"] = "org/private"
        public.write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "invalid"):
            load_development_rows(public, development / "oracle.jsonl")

    def test_development_cli_accepts_no_holdout_path_and_opens_only_dev(self):
        from eval.oracle.build import PackageLimits, SourceGroupLimits, _build_packages

        development = self.root / "development-cli"
        holdout_root = self.root / "holdout-cli"
        public = self.root / "public-cli.json"
        _build_packages(
            self.write_manifest([
                self.seed_entry("org/a", "family-0", "seed-a"),
                self.seed_entry("org/b", "family-1", "seed-b"),
            ]), development, holdout_root, public, b"key" * 16, {}, references=self.references(),
            derive_seed=self.derive, limits=PackageLimits(1, 1, 2.0, 1.0, 1, 2),
            languages=("python",), source_group_limits=SourceGroupLimits(2, 1),
            oracle_kinds=("return-value",),
        )
        holdout = holdout_root / "oracle.jsonl"
        holdout.chmod(0)
        completed = subprocess.run(
            [sys.executable, "-m", "eval.oracle.build", "development",
             "--manifest", str(public), "--package", str(development / "oracle.jsonl")],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )
        holdout.chmod(0o600)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        rows = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all(row["split"] == "dev" for row in rows))


if __name__ == "__main__":
    unittest.main()
