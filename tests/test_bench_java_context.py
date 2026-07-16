import copy
import json
import subprocess
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from eval.bench import java_context, trial


METHOD = """public int value() {
    return helper();
}"""


class JavaContextTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.repo = self.root / "owner" / "repo"
        self.repo.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.email", "test@example.com"],
                       check=True)
        subprocess.run(["git", "-C", str(self.repo), "config", "user.name", "Test"],
                       check=True)
        self.write_java("src/Example.java", (
            "package sample;\n\npublic class Example {\n" + METHOD +
            "\n\n    private int helper() { return 1; }\n}\n"
        ))
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"], check=True)
        self.commit = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True
        ).strip()

    def tearDown(self):
        self.directory.cleanup()

    def write_java(self, relative, text):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def write_bytes(self, relative, data):
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def snapshot(self, message):
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", message], check=True)
        return subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True
        ).strip()

    def pair(self, **changes):
        value = {
            "id": f"owner/repo/{self.commit}/1#0", "func": "value", "code": METHOD,
            "doc": "Returns a value.", "label": "consistent", "category": None,
            "language": "Java",
        }
        value.update(changes)
        return value

    def test_extracts_one_exact_method_window_without_mutating_repository(self):
        before = subprocess.check_output(
            ["git", "-C", str(self.repo), "status", "--porcelain=v1"], text=True
        )

        context = java_context.derive_context(self.pair(), self.root)

        after = subprocess.check_output(
            ["git", "-C", str(self.repo), "status", "--porcelain=v1"], text=True
        )
        self.assertEqual(context["status"], "available")
        self.assertEqual(context["source"]["repo"], "owner/repo")
        self.assertEqual(context["source"]["commit"], self.commit)
        self.assertEqual(context["source"]["path"], "src/Example.java")
        self.assertIn(METHOD, context["snippets"][0]["text"])
        self.assertLessEqual(len(json.dumps(context, sort_keys=True).encode()), 65536)
        self.assertEqual(before, after)

    def test_requires_one_global_exact_match(self):
        self.write_java("src/Copy.java", "class Copy {\n" + METHOD + "\n}\n")
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "copy"], check=True)
        commit = subprocess.check_output(
            ["git", "-C", str(self.repo), "rev-parse", "HEAD"], text=True
        ).strip()

        context = java_context.derive_context(self.pair(
            id=f"owner/repo/{commit}/1#0"
        ), self.root)

        self.assertEqual(context["status"], "unavailable")
        self.assertEqual(context["reason"], "ambiguous-exact-match")

    def test_reports_invalid_identity_and_missing_exact_code_conservatively(self):
        invalid = java_context.derive_context(
            self.pair(id="owner/repo/not-a-commit/1#0"), self.root
        )
        missing = java_context.derive_context(
            self.pair(code="public int value() { return 999; }"), self.root
        )

        self.assertEqual(invalid["reason"], "invalid-pair-id")
        self.assertEqual(missing["reason"], "no-exact-match")

    def test_row_augmentation_changes_only_context_not_labels(self):
        original = self.pair()
        before = copy.deepcopy(original)

        augmented = java_context.augment_rows([original], self.root)

        self.assertEqual(original, before)
        self.assertEqual(augmented[0]["label"], before["label"])
        self.assertEqual(augmented[0]["category"], before["category"])
        self.assertEqual(
            {key: value for key, value in augmented[0].items() if key != "context"}, before
        )

    def test_trial_accepts_only_strict_bounded_context_shape(self):
        pair = self.pair()
        pair["context"] = java_context.derive_context(pair, self.root)
        data = trial._validated_pair_data(pair)
        self.assertEqual(data["context"], pair["context"])

        with self.assertRaisesRegex(ValueError, "context"):
            trial._validated_pair_data({
                **pair, "context": {**pair["context"], "unexpected": True}
            })
        with self.assertRaisesRegex(ValueError, "context"):
            trial._validated_pair_data({
                **pair, "context": {
                    "status": "unavailable", "protocol": "java-git-window-v1",
                    "reason": "x" * 70000,
                },
            })


    # v2 keeps the v1 exact match as rung 1 and adds a token-aware rung 2.

    def v1_v2(self, commit, func, code):
        pair = self.pair(id=f"owner/repo/{commit}/1#0", func=func, code=code)
        return (java_context.derive_context(pair, self.root),
                java_context.derive_context(pair, self.root, java_context.PROTOCOL_V2))

    def test_v2_rung1_reproduces_v1_context_except_the_protocol_string(self):
        v1 = java_context.derive_context(self.pair(), self.root)
        v2 = java_context.derive_context(self.pair(), self.root, java_context.PROTOCOL_V2)
        self.assertEqual(v1["status"], "available")
        self.assertEqual(v2["protocol"], "java-git-window-v2")
        self.assertEqual({**v2, "protocol": v1["protocol"]}, v1)
        self.assertEqual(v2["snippets"][0]["sha256"], v1["snippets"][0]["sha256"])
        self.assertEqual(v2["source"]["sha256"], v1["source"]["sha256"])

    def test_v2_recovers_a_missing_throws_clause(self):
        commit = self.commit_variant(
            "src/Reader.java", "Reader",
            "    public int read(int off) throws java.io.IOException {\n"
            "        return off + 1;\n    }\n")
        v1, v2 = self.v1_v2(commit, "read",
                            "public int read(int off) {\n        return off + 1;\n    }")
        self.assertEqual(v1["reason"], "no-exact-match")
        self.assertEqual(v2["status"], "available")
        self.assertIn("throws java.io.IOException", v2["snippets"][0]["text"])

    def test_v2_recovers_leading_generic_type_parameters(self):
        commit = self.commit_variant(
            "src/Node.java", "Node",
            "    public <T extends Node> T pick(T first) {\n        return first;\n    }\n")
        v1, v2 = self.v1_v2(commit, "pick",
                            "public T pick(T first) {\n        return first;\n    }")
        self.assertEqual(v1["reason"], "no-exact-match")
        self.assertEqual(v2["status"], "available")
        self.assertIn("<T extends Node>", v2["snippets"][0]["text"])

    def test_v2_recovers_token_boundary_spacing(self):
        commit = self.commit_variant(
            "src/Switcher.java", "Switcher",
            "    public int choose(int x) {\n        switch (x) {\n"
            "            default: return 0;\n        }\n    }\n")
        v1, v2 = self.v1_v2(
            commit, "choose",
            "public int choose(int x){\nswitch(x){\ndefault: return 0;\n}\n}")
        self.assertEqual(v1["reason"], "no-exact-match")
        self.assertEqual(v2["status"], "available")

    def test_v2_recovers_stripped_comments(self):
        commit = self.commit_variant(
            "src/Doc.java", "Doc",
            "    public int amount() {\n        // compute it\n"
            "        return 42; /* trailing */\n    }\n")
        v1, v2 = self.v1_v2(commit, "amount",
                            "public int amount() {\n        return 42;\n    }")
        self.assertEqual(v1["reason"], "no-exact-match")
        self.assertEqual(v2["status"], "available")

    def test_v2_will_not_bind_a_same_named_method_with_different_params(self):
        commit = self.commit_variant(
            "src/Over.java", "Over",
            "    public int size(int x) { return x; }\n"
            "    public int size(int x, int y) { return x + y; }\n")
        # Reconstruct a differently-typed overload; the full parameter list must not bind it
        # to either real method even though the name and body text line up.
        v1, v2 = self.v1_v2(commit, "size",
                            "public int size(long x) {\n return x;\n }")
        self.assertEqual(v1["reason"], "no-exact-match")
        self.assertEqual(v2["status"], "unavailable")
        self.assertEqual(v2["reason"], "no-exact-match")

    def test_v2_reads_beyond_the_v1_candidate_ceiling(self):
        self.write_java("src/Real.java",
                        "class Real {\n    public int pick2() {\n        return 5;\n    }\n}\n")
        self.write_java("src/Decoy1.java", "class Decoy1 { /* pick2 referenced */ }\n")
        self.write_java("src/Decoy2.java", "class Decoy2 { int p = pick2Value; }\n")
        commit = self.snapshot("candidates")
        pair = self.pair(id=f"owner/repo/{commit}/1#0", func="pick2",
                         code="public int pick2() {\n        return 5;\n    }")
        with mock.patch.object(java_context, "MAX_CANDIDATES", 2):
            v1 = java_context.derive_context(pair, self.root)
            v2 = java_context.derive_context(pair, self.root, java_context.PROTOCOL_V2)
        self.assertEqual(v1["reason"], "git-command-failed")
        self.assertEqual(v2["status"], "available")

    def test_v2_over_ceiling_reports_too_many_candidates(self):
        self.write_java("src/Real.java",
                        "class Real {\n    public int pick3() {\n        return 5;\n    }\n}\n")
        self.write_java("src/Decoy.java", "class Decoy { int p = pick3Value; }\n")
        commit = self.snapshot("many")
        pair = self.pair(id=f"owner/repo/{commit}/1#0", func="pick3",
                         code="public int pick3() {\n        return 5;\n    }")
        with mock.patch.object(java_context, "MAX_CANDIDATES", 2), \
                mock.patch.object(java_context, "MAX_CANDIDATES_V2", 1):
            v2 = java_context.derive_context(pair, self.root, java_context.PROTOCOL_V2)
        self.assertEqual(v2["status"], "unavailable")
        self.assertEqual(v2["reason"], "too-many-candidates")

    def test_v2_skips_an_undecodable_file_without_the_too_large_mislabel(self):
        self.write_bytes("src/Binary.java",
                         b"class Binary { void garble() {} }\n\xff\xfe\xff\xfe")
        commit = self.snapshot("binary")
        pair = self.pair(id=f"owner/repo/{commit}/1#0", func="garble",
                         code="void garble() {\n return;\n }")
        v1 = java_context.derive_context(pair, self.root)
        v2 = java_context.derive_context(pair, self.root, java_context.PROTOCOL_V2)
        self.assertEqual(v1["reason"], "source-too-large")   # frozen v1 mislabel is preserved
        self.assertEqual(v2["reason"], "no-exact-match")     # v2 skips the binary, no mislabel

    def test_v1_and_v2_contexts_validate_only_under_their_own_protocol(self):
        v1 = java_context.derive_context(self.pair(), self.root)
        v2 = java_context.derive_context(self.pair(), self.root, java_context.PROTOCOL_V2)
        self.assertEqual(java_context.validate_context(v1), v1)
        self.assertEqual(
            java_context.validate_context(v2, java_context.PROTOCOL_V2), v2
        )
        with self.assertRaisesRegex(ValueError, "protocol"):
            java_context.validate_context(v2)                      # default v1 rejects v2
        with self.assertRaisesRegex(ValueError, "protocol"):
            java_context.validate_context(v1, java_context.PROTOCOL_V2)
        with self.assertRaisesRegex(ValueError, "protocol"):
            java_context.validate_context(v1, "java-git-window-v3")
        with self.assertRaisesRegex(ValueError, "protocol"):
            java_context.derive_context(self.pair(), self.root, "java-git-window-v3")

    def commit_variant(self, relative, class_name, body):
        self.write_java(relative,
                        f"package sample;\n\npublic class {class_name} {{\n{body}}}\n")
        return self.snapshot(f"variant-{class_name}")


if __name__ == "__main__":
    unittest.main()
