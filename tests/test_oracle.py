import copy
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class OracleTests(unittest.TestCase):
    maxDiff = None

    def seed(self, language="python", oracle_kind="return-value"):
        from eval.oracle import oracle

        adapter = oracle.LANGUAGE_ADAPTERS[language]
        suffix = {
            "python": ".py",
            "java": ".java",
            "typescript": ".ts",
            "rust": ".rs",
            "go": ".go",
        }[language]
        code = {
            "python": "print(1)\n",
            "java": (
                "class Source { public static void main(String[] args) { "
                "System.out.println(1); } }\n"
            ),
            "typescript": "console.log(1)\n",
            "rust": 'fn main() { println!("1"); }\n',
            "go": 'package main\nimport "fmt"\nfunc main() { fmt.Println(1) }\n',
        }[language]
        before = "1"
        after = "2"
        offset = code.index(before)
        source_bytes = code.encode()
        mutation_bytes = source_bytes[:offset] + after.encode() + source_bytes[offset + 1:]
        noop_bytes = source_bytes + oracle.semantic_noop_suffix(language)
        documentation = "Returns the value 1."
        image = f"registry.invalid/evergreen-{language}@sha256:" + "a" * 64
        seed = {
            "schema_version": 1,
            "kind": "evergreen-executable-documentation-oracle",
            "group_id": f"oracle-{language}-001",
            "project": "example/project",
            "source": {
                "origin": "https://example.invalid/example/project.git",
                "commit": "b" * 40,
                "license": "MIT",
                "path": f"fixture/source{suffix}",
                "code": code,
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            },
            "language": language,
            "documentation": {
                "template": documentation,
                "sha256": hashlib.sha256(documentation.encode()).hexdigest(),
            },
            "harness": {
                "argv": [adapter, f"/input/fixture/source{suffix}"],
            },
            "oracle": {
                "kind": oracle_kind,
                "expected_observable": {"exit_code": 0, "stdout": "1\n"},
            },
            "mutation": {
                "id": f"{oracle_kind}-replace-v1",
                "offset": offset,
                "before": before,
                "after": after,
                "derivative_sha256": hashlib.sha256(mutation_bytes).hexdigest(),
            },
            "semantic_noop": {
                "id": "comment-v1",
                "derivative_sha256": hashlib.sha256(noop_bytes).hexdigest(),
            },
            "sandbox": {
                "engine": "docker",
                "image": image,
                "profile": "evergreen-oracle-v1",
            },
        }
        seed["seed_sha256"] = oracle.seed_sha256(seed)
        return seed

    def observations(self, seed):
        from eval.oracle import oracle

        expected = seed["oracle"]["expected_observable"]
        return [
            {**expected, "stderr": ""},
            {
                "exit_code": oracle.ORACLE_MISMATCH_EXIT,
                "stdout": oracle.mismatch_stdout(seed["oracle"]["kind"]),
                "stderr": "",
            },
            {**expected, "stderr": ""},
        ]

    def test_five_languages_derive_only_runtime_labels_with_fixed_docs_and_group(self):
        from eval.oracle import oracle

        for language in oracle.LANGUAGES:
            with self.subTest(language=language):
                seed = self.seed(language)
                approved = {language: seed["sandbox"]["image"]}
                with mock.patch.object(
                    oracle, "_execute_variant", side_effect=self.observations(seed)
                ) as execute, mock.patch.object(
                    oracle, "_docker_engine", return_value=Path("/usr/local/bin/docker")
                ):
                    rows = oracle.run_seed(seed, approved_images=approved)

                self.assertEqual([row["label"] for row in rows], [
                    "consistent", "inconsistent", "consistent",
                ])
                self.assertEqual([row["variant"] for row in rows], [
                    "source", "mutation", "semantic-noop",
                ])
                self.assertEqual({row["group_id"] for row in rows}, {seed["group_id"]})
                self.assertEqual(
                    {row["documentation"] for row in rows},
                    {seed["documentation"]["template"]},
                )
                self.assertEqual(execute.call_count, 3)
                self.assertNotEqual(rows[0]["code"], rows[1]["code"])
                self.assertNotEqual(rows[0]["code"], rows[2]["code"])

    def test_supports_exactly_the_five_versioned_observable_kinds(self):
        from eval.oracle import oracle

        for kind in (
            "return-value", "raises", "default-value", "cardinality", "state-change",
        ):
            with self.subTest(kind=kind):
                seed = self.seed(oracle_kind=kind)
                oracle.validate_seed(seed)
        seed = self.seed()
        seed["oracle"]["kind"] = "free-form-opinion"
        seed["mutation"]["id"] = "free-form-opinion-replace-v1"
        seed["seed_sha256"] = oracle.seed_sha256(seed)
        with self.assertRaisesRegex(oracle.OracleError, "oracle kind"):
            oracle.validate_seed(seed)

    def test_input_cannot_supply_label_or_verdict_at_any_depth(self):
        from eval.oracle import oracle

        for path in (("label",), ("source", "verdict")):
            with self.subTest(path=path):
                seed = self.seed()
                target = seed
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = "consistent"
                seed["seed_sha256"] = oracle.seed_sha256(seed)
                with self.assertRaisesRegex(oracle.OracleError, "label or verdict"):
                    oracle.validate_seed(seed)

    def test_harness_and_sandbox_command_are_fixed_shell_free_and_network_free(self):
        from eval.oracle import oracle

        seed = self.seed("go")
        command = oracle._sandbox_command(
            seed, Path("/private/tmp/oracle-fixture"), "evergreen-oracle-deadbeef",
            Path("/usr/local/bin/docker"),
        )

        self.assertEqual(command[0], "/usr/local/bin/docker")
        self.assertIn("--network=none", command)
        self.assertIn("--read-only", command)
        self.assertIn("--cap-drop=ALL", command)
        self.assertIn("--security-opt=no-new-privileges", command)
        self.assertIn("--pids-limit=64", command)
        self.assertIn("--memory=256m", command)
        self.assertIn("--cpus=1", command)
        self.assertIn(seed["sandbox"]["image"], command)
        self.assertEqual(command[-2:], seed["harness"]["argv"])
        self.assertNotIn("sh", [Path(token).name for token in command])

        for bad in (
            ["sh", "-c", "go run source.go"],
            [oracle.LANGUAGE_ADAPTERS["go"], "/input/fixture/source.go", "extra"],
            [oracle.LANGUAGE_ADAPTERS["python"], "/input/fixture/source.go"],
        ):
            with self.subTest(argv=bad):
                changed = self.seed("go")
                changed["harness"]["argv"] = bad
                changed["seed_sha256"] = oracle.seed_sha256(changed)
                with self.assertRaisesRegex(oracle.OracleError, "harness argv"):
                    oracle.validate_seed(changed)

    def test_hash_binding_rejects_changed_source_docs_mutation_and_noop(self):
        from eval.oracle import oracle

        changes = (
            lambda value: value["source"].__setitem__("code", "value = 9\n"),
            lambda value: value["documentation"].__setitem__("template", "Returns nine."),
            lambda value: value["mutation"].__setitem__("derivative_sha256", "0" * 64),
            lambda value: value["semantic_noop"].__setitem__("derivative_sha256", "0" * 64),
        )
        for change in changes:
            seed = self.seed()
            change(seed)
            seed["seed_sha256"] = oracle.seed_sha256(seed)
            with self.assertRaisesRegex(oracle.OracleError, "SHA-256"):
                oracle.validate_seed(seed)

        seed = self.seed()
        seed["source"]["code"] += " "
        with self.assertRaisesRegex(oracle.OracleError, "SHA-256"):
            oracle.validate_seed(seed)

    def test_unknown_or_ambiguous_mutation_and_observable_are_invalid(self):
        from eval.oracle import oracle

        cases = []
        unknown = self.seed()
        unknown["mutation"]["id"] = "arbitrary-code-v1"
        cases.append((unknown, "mutation"))
        wrong_before = self.seed()
        wrong_before["mutation"]["before"] = "3"
        cases.append((wrong_before, "mutation"))
        ambiguous = self.seed()
        ambiguous["oracle"]["expected_observable"] = {
            "exit_code": oracle.ORACLE_MISMATCH_EXIT,
            "stdout": oracle.mismatch_stdout("return-value"),
        }
        cases.append((ambiguous, "observable"))
        for seed, message in cases:
            with self.subTest(message=message):
                seed["seed_sha256"] = oracle.seed_sha256(seed)
                with self.assertRaisesRegex(oracle.OracleError, message):
                    oracle.validate_seed(seed)

    def test_compile_failure_timeout_extra_output_and_wrong_mismatch_fail_closed(self):
        from eval.oracle import oracle

        seed = self.seed()
        approved = {"python": seed["sandbox"]["image"]}
        invalid = (
            {"exit_code": 125, "stdout": "compile failed\n", "stderr": ""},
            {"exit_code": 0, "stdout": "1\nextra\n", "stderr": ""},
            {"exit_code": oracle.ORACLE_MISMATCH_EXIT, "stdout": "wrong\n", "stderr": ""},
            {"exit_code": 0, "stdout": "1\n", "stderr": "warning\n"},
        )
        for result in invalid:
            with self.subTest(result=result):
                observations = self.observations(seed)
                observations[1] = result
                with mock.patch.object(
                    oracle, "_execute_variant", side_effect=observations
                ), mock.patch.object(
                    oracle, "_docker_engine", return_value=Path("/usr/local/bin/docker")
                ), self.assertRaisesRegex(oracle.OracleError, "structured oracle mismatch"):
                    oracle.run_seed(seed, approved_images=approved)

        with mock.patch.object(
            oracle, "_execute_variant", side_effect=oracle.OracleOperationalError("timed out")
        ), mock.patch.object(
            oracle, "_docker_engine", return_value=Path("/usr/local/bin/docker")
        ), self.assertRaisesRegex(oracle.OracleOperationalError, "timed out"):
            oracle.run_seed(seed, approved_images=approved)

    def test_cleanup_failure_invalidates_execution(self):
        from eval.oracle import oracle

        seed = self.seed()
        with mock.patch.object(
            oracle, "_bounded_container", return_value={
                "exit_code": 0, "stdout": "1\n", "stderr": "",
            }
        ), mock.patch.object(
            oracle, "_remove_container", side_effect=oracle.OracleOperationalError(
                "sandbox cleanup failed"
            )
        ), self.assertRaisesRegex(oracle.OracleOperationalError, "cleanup failed"):
            oracle._execute_variant(
                seed, seed["source"]["code"].encode(), Path("/usr/local/bin/docker")
            )

    def test_unapproved_image_or_missing_engine_fails_before_execution(self):
        from eval.oracle import oracle

        seed = self.seed()
        with self.assertRaisesRegex(oracle.OracleError, "approved sandbox image"):
            oracle.run_seed(seed, approved_images={})
        with mock.patch.object(oracle, "_docker_engine", return_value=None), \
                self.assertRaisesRegex(oracle.OracleOperationalError, "sandbox engine"):
            oracle.run_seed(
                seed, approved_images={"python": seed["sandbox"]["image"]}
            )

    def test_schema_is_exact_and_contains_no_input_label_or_verdict(self):
        schema = json.loads((ROOT / "eval" / "oracle" / "schema-v1.json").read_text())

        self.assertEqual(schema["properties"]["schema_version"], {"const": 1})
        self.assertFalse(schema["additionalProperties"])
        encoded = json.dumps(schema, sort_keys=True)
        self.assertNotIn('"label"', encoded)
        self.assertNotIn('"verdict"', encoded)


if __name__ == "__main__":
    unittest.main()
