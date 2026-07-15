import copy
import hashlib
import json
from pathlib import Path
import re
import stat
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
        return_code = {
            "python": "def value(): return 1\nprint(value())\n",
            "java": (
                "class Source { static int value() { return 1; } "
                "public static void main(String[] args) { System.out.println(value()); } }\n"
            ),
            "typescript": "function value() { return 1; }\nconsole.log(value())\n",
            "rust": 'fn value() -> i32 { return 1; }\nfn main() { println!("{}", value()); }\n',
            "go": ('package main\nimport "fmt"\nfunc value() int { return 1 }\n'
                   'func main() { fmt.Println(value()) }\n'),
        }[language]
        kind_code = {
            "raises": {
                "python": ('try:\n    if False: raise ValueError()\n    print("no-error")\n'
                           'except ValueError:\n    print("ValueError")\n'),
                "java": ('class Source { public static void main(String[] a) { try { '
                         'if (false) throw new IllegalStateException(); '
                         'System.out.println("no-error"); } catch (Exception e) { '
                         'System.out.println("ValueError"); } } }\n'),
                "typescript": ('try { if (false) { throw new Error(); } '
                               'console.log("no-error"); } catch { console.log("ValueError"); }\n'),
                "rust": ('fn main() { let result = std::panic::catch_unwind(|| { '
                         'if false { panic!("x"); } }); println!("{}", '
                         'if result.is_ok() { "no-error" } else { "ValueError" }); }\n'),
                "go": ('package main\nimport "fmt"\nfunc main() { defer func() { if recover() '
                       '!= nil { fmt.Println("ValueError") } }(); if false { panic("x") }; '
                       'fmt.Println("no-error") }\n'),
            },
            "default-value": {
                "python": 'def value(item=1): return item\nprint(f"default:{value()}")\n',
                "java": ('import java.util.Optional; class Source { public static void main('
                         'String[] a) { System.out.println("default:" + '
                         'Optional.ofNullable(null).orElse(1)); } }\n'),
                "typescript": ('function value(item = 1) { return item; } '
                               'console.log(`default:${value()}`);\n'),
                "rust": ('fn main() { let value = None::<i32>.unwrap_or(1); '
                         'println!("default:{}", value); }\n'),
                "go": ('package main\nimport "fmt"\nfunc defaultValue(v int) int { return v }\n'
                       'func main() { fmt.Printf("default:%d\\n", defaultValue(1)) }\n'),
            },
            "cardinality": {
                "python": 'items = [1]\nprint(f"cardinality:{len(items)}")\n',
                "java": ('class Source { public static void main(String[] a) { int[] items = {1}; '
                         'System.out.println("cardinality:" + items.length); } }\n'),
                "typescript": ('const items = [1]; console.log(`cardinality:${items.length}`);\n'),
                "rust": ('fn main() { let items = [1]; println!("cardinality:{}", items.len()); }\n'),
                "go": ('package main\nimport "fmt"\nfunc main() { items := []int{1}; '
                       'fmt.Printf("cardinality:%d\\n", len(items)) }\n'),
            },
            "state-change": {
                "python": ('state = False\nstate = not state\n'
                           'print(f"state:{\'changed\' if state else \'unchanged\'}")\n'),
                "java": ('class Source { public static void main(String[] a) { boolean state = '
                         'false; state = !state; System.out.println("state:" + '
                         '(state ? "changed" : "unchanged")); } }\n'),
                "typescript": ('let state = false; state = !state; '
                               'console.log(`state:${state ? "changed" : "unchanged"}`);\n'),
                "rust": ('fn main() { let mut state = false; state = !state; println!("state:{}", '
                         'if state { "changed" } else { "unchanged" }); }\n'),
                "go": ('package main\nimport "fmt"\nfunc main() { state := false; state = !state; '
                       'if state { fmt.Println("state:changed") } else { '
                       'fmt.Println("state:unchanged") } }\n'),
            },
        }
        if oracle_kind == "return-value":
            code = return_code
        elif oracle_kind in kind_code:
            code = kind_code[oracle_kind][language]
        else:
            raise ValueError("test fixture does not define that language/kind combination")
        operator_id = {
            contract["kind"]: identity for identity, contract in oracle.MUTATION_OPERATORS.items()
        }[oracle_kind]
        contract = oracle.MUTATION_OPERATORS[operator_id]
        variant = contract["variants"][language]
        before = variant["before"]
        after = variant["after"]
        offset = code.encode().index(before)
        source_bytes = code.encode()
        mutation_bytes = source_bytes[:offset] + after + source_bytes[offset + len(before):]
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
                "argv": [adapter, f"/input/fixture/source{suffix}", oracle.CONTROL_PATH],
            },
            "oracle": {
                "kind": oracle_kind,
                "expected_observable": {
                    "exit_code": contract["expected_observable"][0],
                    "stdout": contract["expected_observable"][1],
                },
            },
            "mutation": {
                "operator": operator_id,
                "offset": offset,
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

        source = seed["source"]["code"].encode()
        mutation = oracle._mutated_source(seed)
        noop = source + oracle.semantic_noop_suffix(seed["language"])
        contract = oracle.MUTATION_OPERATORS[seed["mutation"]["operator"]]
        return [
            self.adapter_result(seed, source, "match", {
                "exit_code": contract["expected_observable"][0],
                "stdout": contract["expected_observable"][1], "stderr": "",
            }),
            self.adapter_result(seed, mutation, "mismatch", {
                "exit_code": contract["mutated_observable"][0],
                "stdout": contract["mutated_observable"][1], "stderr": "",
            }),
            self.adapter_result(seed, noop, "match", {
                "exit_code": contract["expected_observable"][0],
                "stdout": contract["expected_observable"][1], "stderr": "",
            }),
        ]

    def adapter_result(self, seed, source_bytes, verdict, observed):
        from eval.oracle import oracle

        spec = oracle._control_spec(seed, source_bytes)
        payload = {
            "schema_version": 1,
            "protocol": oracle.ADAPTER_PROTOCOL,
            "control_sha256": spec["control_sha256"],
            "source_sha256": spec["source_sha256"],
            "operator_id": spec["operator_id"],
            "phase": "observed",
            "verdict": verdict,
            "observed": observed,
        }
        return {
            "exit_code": 0,
            "stdout": json.dumps(
                payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
            ) + "\n",
            "stderr": "",
        }

    def container_observation(self, command, _environment, timeout=0):
        from eval.oracle import oracle

        del timeout
        control_mount = next(token for token in command if "dst=/control" in token)
        fields = dict(
            field.split("=", 1)
            for field in control_mount.removeprefix("--mount=").split(",")
            if "=" in field
        )
        spec = json.loads((Path(fields["src"]) / "oracle-v1.json").read_text())
        mutation = spec["operator_id"] in oracle.MUTATION_OPERATORS
        contract = oracle.MUTATION_OPERATORS.get(spec["operator_id"])
        observed = {
            "exit_code": 0,
            "stdout": (contract["mutated_observable"][1] if mutation else
                       spec["expected_observable"]["stdout"]),
            "stderr": "",
        }
        payload = {
            "schema_version": 1,
            "protocol": "evergreen-oracle-adapter-result-v1",
            "control_sha256": spec["control_sha256"],
            "source_sha256": spec["source_sha256"],
            "operator_id": spec["operator_id"],
            "phase": "observed",
            "verdict": "mismatch" if mutation else "match",
            "observed": observed,
        }
        return {
            "exit_code": 0,
            "stdout": json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
            "stderr": "",
        }

    def test_five_languages_derive_only_runtime_labels_with_fixed_docs_and_group(self):
        from eval.oracle import oracle

        for language in oracle.LANGUAGES:
            with self.subTest(language=language):
                seed = self.seed(language)
                approved = {language: seed["sandbox"]["image"]}
                with mock.patch.object(
                    oracle, "_bounded_container", side_effect=self.container_observation,
                ) as execute, mock.patch.object(
                    oracle, "_remove_container"
                ), mock.patch.object(
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
        seed["seed_sha256"] = oracle.seed_sha256(seed)
        with self.assertRaisesRegex(oracle.OracleError, "oracle kind"):
            oracle.validate_seed(seed)

    def test_each_oracle_kind_has_one_distinct_bound_operator_contract(self):
        from eval.oracle import oracle

        expected = {
            "return-value": "return-value-1-to-2-v1",
            "raises": "raises-none-to-value-error-v1",
            "default-value": "default-value-one-to-two-v1",
            "cardinality": "cardinality-one-to-two-v1",
            "state-change": "state-change-before-to-after-v1",
        }
        self.assertEqual(
            {contract["kind"]: operator for operator, contract in oracle.MUTATION_OPERATORS.items()},
            expected,
        )
        self.assertEqual(len({
            (contract["expected_observable"], contract["mutated_observable"], tuple(
                (language, variant["before"], variant["after"], variant["source_pattern"])
                for language, variant in sorted(contract["variants"].items())
            )) for contract in oracle.MUTATION_OPERATORS.values()
        }), 5)

        generic = self.seed(oracle_kind="return-value")
        for relabeled in tuple(expected)[1:]:
            changed = copy.deepcopy(generic)
            changed["oracle"]["kind"] = relabeled
            changed["seed_sha256"] = oracle.seed_sha256(changed)
            with self.subTest(relabeled=relabeled), self.assertRaisesRegex(
                oracle.OracleError, "operator contract"
            ):
                oracle.validate_seed(changed)

    def test_all_twenty_five_language_kind_contracts_validate_and_derive(self):
        from eval.oracle import oracle

        for language in oracle.LANGUAGES:
            for kind in oracle.ORACLE_KINDS:
                with self.subTest(language=language, kind=kind):
                    seed = self.seed(language, kind)
                    oracle.validate_seed(seed)
                    with mock.patch.object(
                        oracle, "_bounded_container", side_effect=self.container_observation,
                    ), mock.patch.object(oracle, "_remove_container"), mock.patch.object(
                        oracle, "_docker_engine", return_value=Path("/usr/local/bin/docker"),
                    ):
                        rows = oracle.run_seed(
                            seed, approved_images={language: seed["sandbox"]["image"]},
                        )
                    self.assertEqual(rows[1]["oracle_kind"], kind)
                    self.assertEqual(rows[1]["mutation_id"], seed["mutation"]["operator"])

        for kind in oracle.ORACLE_KINDS:
            cross_language = self.seed("python", kind)
            cross_language["language"] = "java"
            cross_language["source"]["path"] = "fixture/source.java"
            cross_language["harness"]["argv"] = [
                oracle.LANGUAGE_ADAPTERS["java"], "/input/fixture/source.java",
                oracle.CONTROL_PATH,
            ]
            source = cross_language["source"]["code"].encode()
            noop = source + oracle.semantic_noop_suffix("java")
            cross_language["semantic_noop"]["derivative_sha256"] = hashlib.sha256(noop).hexdigest()
            cross_language["seed_sha256"] = oracle.seed_sha256(cross_language)
            with self.subTest(cross_language_kind=kind), self.assertRaisesRegex(
                oracle.OracleError, "source pattern|mutation"
            ):
                oracle.validate_seed(cross_language)

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
            seed, Path("/private/tmp/oracle-input"), Path("/private/tmp/oracle-control"),
            "evergreen-oracle-deadbeef", Path("/usr/local/bin/docker"),
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
        self.assertEqual(command[-len(seed["harness"]["argv"]):], seed["harness"]["argv"])
        self.assertNotIn("sh", [Path(token).name for token in command])

        for bad in (
            ["sh", "-c", "go run source.go"],
            [oracle.LANGUAGE_ADAPTERS["go"], "/input/fixture/source.go", "extra"],
            [oracle.LANGUAGE_ADAPTERS["python"], "/input/fixture/source.go", oracle.CONTROL_PATH],
        ):
            with self.subTest(argv=bad):
                changed = self.seed("go")
                changed["harness"]["argv"] = bad
                changed["seed_sha256"] = oracle.seed_sha256(changed)
                with self.assertRaisesRegex(oracle.OracleError, "harness argv"):
                    oracle.validate_seed(changed)

    def test_fixture_mounts_are_readable_by_only_the_unprivileged_adapter(self):
        from eval.oracle import oracle

        seed = self.seed()

        def inspect_fixture(command, _environment, timeout=0):
            del timeout
            mounts = {}
            for token in command:
                if not token.startswith("--mount="):
                    continue
                fields = dict(
                    field.split("=", 1) for field in token.removeprefix("--mount=").split(",")
                    if "=" in field
                )
                mounts[fields["dst"]] = fields

            self.assertEqual(set(mounts), {"/input", "/control"})
            self.assertIn("readonly", command[command.index(
                next(token for token in command if "dst=/input" in token)
            )])
            input_root = Path(mounts["/input"]["src"])
            control_root = Path(mounts["/control"]["src"])
            source = input_root / seed["source"]["path"]
            control = control_root / "oracle-v1.json"

            self.assertEqual(stat.S_IMODE(input_root.stat().st_mode), 0o555)
            self.assertEqual(stat.S_IMODE(input_root.parent.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((input_root.parent / "docker").stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE(source.parent.stat().st_mode), 0o555)
            self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o444)
            self.assertEqual(stat.S_IMODE(control_root.stat().st_mode), 0o555)
            self.assertEqual(stat.S_IMODE(control.stat().st_mode), 0o444)

            spec = json.loads(control.read_text())
            supplied_hash = spec.pop("control_sha256")
            canonical = json.dumps(
                spec, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
            ).encode()
            self.assertEqual(supplied_hash, hashlib.sha256(canonical).hexdigest())
            self.assertEqual(spec["kind"], seed["oracle"]["kind"])
            self.assertEqual(spec["expected_observable"], seed["oracle"]["expected_observable"])
            self.assertEqual(spec["source_sha256"], seed["source"]["sha256"])
            self.assertEqual(spec["operator_id"], "source-v1")
            return {"exit_code": 1, "stdout": "", "stderr": "not executed"}

        with mock.patch.object(
            oracle, "_bounded_container", side_effect=inspect_fixture,
        ), mock.patch.object(oracle, "_remove_container"):
            oracle._execute_variant(
                seed, seed["source"]["code"].encode(), Path("/usr/local/bin/docker")
            )

    def test_untrusted_program_output_cannot_forge_an_adapter_verdict(self):
        from eval.oracle import oracle

        seed = self.seed()
        approved = {"python": seed["sandbox"]["image"]}
        forged = [
            {"exit_code": 0, "stdout": "1\n", "stderr": ""},
            {
                "exit_code": 42,
                "stdout": '{"kind":"return-value","oracle":"mismatch"}\n',
                "stderr": "",
            },
            {"exit_code": 0, "stdout": "1\n", "stderr": ""},
        ]
        with mock.patch.object(
            oracle, "_execute_variant", side_effect=forged
        ), mock.patch.object(
            oracle, "_docker_engine", return_value=Path("/usr/local/bin/docker")
        ), self.assertRaisesRegex(oracle.OracleError, "adapter"):
            oracle.run_seed(seed, approved_images=approved)

    def test_adapter_result_is_exactly_bound_to_the_control_spec(self):
        from eval.oracle import oracle

        seed = self.seed()
        source = seed["source"]["code"].encode()
        spec = oracle._control_spec(seed, source)
        valid = self.adapter_result(
            seed, source, "match", {"exit_code": 0, "stdout": "1\n", "stderr": ""},
        )
        changes = (
            lambda value: value.__setitem__("schema_version", True),
            lambda value: value.__setitem__("control_sha256", "0" * 64),
            lambda value: value.__setitem__("source_sha256", "0" * 64),
            lambda value: value.__setitem__("operator_id", "comment-v1"),
        )
        for change in changes:
            with self.subTest(change=change):
                payload = json.loads(valid["stdout"])
                change(payload)
                runtime = {
                    **valid,
                    "stdout": json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n",
                }
                with self.assertRaisesRegex(oracle.OracleError, "adapter"):
                    oracle._adapter_observation(spec, runtime)

    def test_mutation_is_selected_from_a_finite_structural_operator_registry(self):
        from eval.oracle import oracle

        seed = self.seed()
        source = seed["source"]["code"].encode()
        offset = source.index(b"print")
        derivative = source[:offset] + b"other" + source[offset + len(b"print"):]
        seed["mutation"].update({
            "operator": "identifier-print-to-other-v1",
            "offset": offset,
            "derivative_sha256": hashlib.sha256(derivative).hexdigest(),
        })
        seed["seed_sha256"] = oracle.seed_sha256(seed)

        with self.assertRaisesRegex(oracle.OracleError, "mutation operator"):
            oracle.validate_seed(seed)

        embedded = self.seed()
        code = "print(10)\n"
        embedded["source"]["code"] = code
        embedded["source"]["sha256"] = hashlib.sha256(code.encode()).hexdigest()
        embedded["mutation"]["offset"] = code.index("1")
        derivative = code.replace("1", "2", 1).encode()
        embedded["mutation"]["derivative_sha256"] = hashlib.sha256(derivative).hexdigest()
        noop = code.encode() + oracle.semantic_noop_suffix("python")
        embedded["semantic_noop"]["derivative_sha256"] = hashlib.sha256(noop).hexdigest()
        embedded["seed_sha256"] = oracle.seed_sha256(embedded)
        with self.assertRaisesRegex(oracle.OracleError, "source pattern|mutation"):
            oracle.validate_seed(embedded)

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
        unknown["mutation"]["operator"] = "arbitrary-code-v1"
        cases.append((unknown, "mutation"))
        wrong_before = self.seed()
        wrong_before["mutation"]["offset"] += 1
        cases.append((wrong_before, "mutation"))
        ambiguous = self.seed()
        ambiguous["oracle"]["expected_observable"] = {
            "exit_code": 42,
            "stdout": '{"kind":"return-value","oracle":"mismatch"}\n',
        }
        cases.append((ambiguous, "observable"))
        for seed, message in cases:
            with self.subTest(message=message):
                seed["seed_sha256"] = oracle.seed_sha256(seed)
                with self.assertRaisesRegex(oracle.OracleError, message):
                    oracle.validate_seed(seed)

        hidden = self.seed()
        hidden["source"]["path"] = ".hidden.py"
        hidden["harness"]["argv"][1] = "/input/.hidden.py"
        hidden["seed_sha256"] = oracle.seed_sha256(hidden)
        with self.assertRaisesRegex(oracle.OracleError, "source path"):
            oracle.validate_seed(hidden)

    def test_compile_failure_timeout_extra_output_and_wrong_mismatch_fail_closed(self):
        from eval.oracle import oracle

        seed = self.seed()
        approved = {"python": seed["sandbox"]["image"]}
        source = seed["source"]["code"].encode()
        mutation = oracle._mutated_source(seed)
        contract = oracle.MUTATION_OPERATORS[seed["mutation"]["operator"]]
        invalid = (
            {"exit_code": 125, "stdout": "compile failed\n", "stderr": ""},
            self.adapter_result(seed, mutation, "mismatch", {
                "exit_code": 0, "stdout": contract["mutated_observable"][1] + "extra\n",
                "stderr": "",
            }),
            self.adapter_result(seed, mutation, "match", {
                "exit_code": 0, "stdout": contract["mutated_observable"][1], "stderr": "",
            }),
            {"exit_code": 0, "stdout": "{}\n", "stderr": "warning\n"},
        )
        for result in invalid:
            with self.subTest(result=result):
                observations = self.observations(seed)
                observations[1] = result
                with mock.patch.object(
                    oracle, "_execute_variant", side_effect=observations
                ), mock.patch.object(
                    oracle, "_docker_engine", return_value=Path("/usr/local/bin/docker")
                ), self.assertRaisesRegex(oracle.OracleError, "adapter"):
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

        argv = schema["properties"]["harness"]["properties"]["argv"]
        self.assertEqual(argv.get("prefixItems"), [
            {"enum": [
                "/opt/evergreen/bin/python-oracle-v1",
                "/opt/evergreen/bin/java-oracle-v1",
                "/opt/evergreen/bin/typescript-oracle-v1",
                "/opt/evergreen/bin/rust-oracle-v1",
                "/opt/evergreen/bin/go-oracle-v1",
            ]},
            {
                "pattern": "^/input/[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+)*(?:/[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+)*)*$",
                "type": "string",
            },
            {"const": "/control/oracle-v1.json"},
        ])
        self.assertIs(argv.get("items"), False)
        path_pattern = re.compile(argv["prefixItems"][1]["pattern"])
        for forbidden in (
            "/input/../escape.py", "/input/fixture/../../escape.py", "/input/.hidden.py",
        ):
            with self.subTest(forbidden_schema_path=forbidden):
                self.assertIsNone(path_pattern.fullmatch(forbidden))
        self.assertIsNotNone(path_pattern.fullmatch("/input/fixture/source.py"))
        self.assertEqual(set(
            schema["properties"]["mutation"]["properties"]["operator"]["enum"]
        ), {
            "return-value-1-to-2-v1", "raises-none-to-value-error-v1",
            "default-value-one-to-two-v1", "cardinality-one-to-two-v1",
            "state-change-before-to-after-v1",
        })
        self.assertEqual(len(schema.get("allOf", [])), 35)
        bound_pairs = set()
        for branch in schema["allOf"][10:]:
            conditions = branch["if"]["properties"]
            bound_pairs.add((
                conditions["language"]["const"],
                conditions["mutation"]["properties"]["operator"]["const"],
            ))
            self.assertIn("pattern", branch["then"]["properties"]["source"]
                          ["properties"]["code"])
        self.assertEqual(len(bound_pairs), 25)

    def test_schema_source_path_is_normalized_repository_relative(self):
        schema = json.loads((ROOT / "eval" / "oracle" / "schema-v1.json").read_text())
        expected = (
            "^[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+)*"
            "(?:/[A-Za-z0-9_-]+(?:\\.[A-Za-z0-9_-]+)*)*$"
        )
        source_path = schema["properties"]["source"]["properties"]["path"]
        self.assertEqual(source_path.get("pattern"), expected)

        pattern = re.compile(source_path["pattern"])
        for forbidden in ("../escape.py", "/absolute.py", ".hidden.py", "fixture\\source.py"):
            with self.subTest(forbidden_schema_source_path=forbidden):
                self.assertIsNone(pattern.fullmatch(forbidden))
        self.assertIsNotNone(pattern.fullmatch("fixture/source.py"))


if __name__ == "__main__":
    unittest.main()
