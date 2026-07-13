import copy
import json
import subprocess
import tempfile
from pathlib import Path
import unittest

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


if __name__ == "__main__":
    unittest.main()
