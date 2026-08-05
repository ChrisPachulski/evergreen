import ast
from pathlib import Path
import unittest

from evergreen.execution_policy import classify_command


ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "evergreen" / "execution_policy.py"
CONTRACT = (
    "Run only a repository-declared test command with a bounded timeout.",
    "Use a disposable scratch location and remove it only through the host's safe cleanup mechanism.",
    "Do not add, print, or forward secrets; declare any existing secret dependency before execution.",
    "Disable network access when the host can do so safely; otherwise declare the network requirement before execution.",
    "Refuse privileged, destructive, cleanup, deployment, upload, push, publication, and portal-mutation commands.",
    "If the command, isolation, timeout, dependencies, or test setup cannot be trusted, report inconclusive, never drift.",
    "Classifier output is advisory: allowed still requires the runtime safeguards above.",
)


class ExecutionPolicyTests(unittest.TestCase):
    def test_safe_execution_contract_is_identical_across_agent_surfaces(self):
        surfaces = (
            ROOT / "skills" / "evergreen" / "SKILL.md",
            ROOT / "skills" / "evergreen" / "DIGEST.md",
            ROOT / "AGENTS.md",
            ROOT / "commands" / "winnow.md",
        )
        for sentence in CONTRACT:
            for surface in surfaces:
                with self.subTest(sentence=sentence, surface=surface.name):
                    self.assertIn(sentence, surface.read_text())

    def test_no_module_level_private_helper_is_unreferenced(self):
        tree = ast.parse(POLICY.read_text())
        defined = {
            node.name for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_")
        }
        referenced = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        self.assertEqual(sorted(defined - referenced), [])


class ClassifyCommandTests(unittest.TestCase):
    """The classifier is a documented public helper with no in-repo caller."""

    def assertClassifies(self, cases):
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(classify_command(argv), expected)

    def test_recognized_test_drivers_are_allowed(self):
        self.assertClassifies((
            (["pytest", "-q"], "allowed"),
            (["python3", "-m", "pytest"], "allowed"),
            (["python", "-m", "unittest"], "allowed"),
            (["npm", "test"], "allowed"),
            (["npm", "run", "test:unit"], "allowed"),
            (["cargo", "test"], "allowed"),
            (["bundle", "exec", "rspec"], "allowed"),
            (["timeout", "60", "pytest"], "allowed"),
        ))

    def test_privileged_destructive_and_operation_commands_are_refused(self):
        self.assertClassifies((
            (["sudo", "pytest"], "refused"),
            (["rm", "-rf", "build"], "refused"),
            (["git", "push"], "refused"),
            (["git", "clean", "-fd"], "refused"),
            (["npm", "publish"], "refused"),
            (["twine", "upload", "dist"], "refused"),
            (["kubectl", "apply", "-f", "svc.yaml"], "refused"),
            (["fastlane", "beta"], "refused"),
            (["xcrun", "altool", "--upload-app"], "refused"),
            (["./scripts/deploy.sh", "--all"], "refused"),
            (["pytest", "--testflight-lane"], "refused"),
        ))

    def test_shell_syntax_is_refused_without_interpretation(self):
        self.assertClassifies((
            (["pytest; rm -rf /"], "refused"),
            (["pytest", "&&", "npm", "publish"], "refused"),
            (["pytest", "-k", "a|b"], "refused"),
            (["pytest", "$(id)"], "refused"),
            (["pytest", "> out.txt"], "refused"),
            (["pytest", "a\nb"], "refused"),
        ))

    def test_untrusted_shapes_are_inconclusive_never_allowed(self):
        self.assertClassifies((
            ([], "inconclusive"),
            ("pytest", "inconclusive"),
            (["pytest", ""], "inconclusive"),
            (["pytest"] * 129, "inconclusive"),
            (["./run-tests"], "inconclusive"),
            (["docker", "run", "pytest"], "inconclusive"),
            (["env", "pytest"], "inconclusive"),
            (["pytest", "--network"], "inconclusive"),
            (["pytest", "--token=abc123"], "inconclusive"),
            (["pytest", "https://example.test/suite"], "inconclusive"),
            (["timeout", "5000", "pytest"], "inconclusive"),
            (["timeout", "0", "pytest"], "inconclusive"),
            (["timeout", "60"], "inconclusive"),
            (["timeout", "sixty", "pytest"], "inconclusive"),
        ))

    def test_source_paths_named_after_operations_stay_allowed(self):
        """Component screening must not refuse a test file called `deploy`."""
        self.assertClassifies((
            (["pytest", "tests/test_deploy.py"], "allowed"),
            (["pytest", "tests/test_release.py::test_upload"], "allowed"),
            (["pytest", "docs/publish.md"], "allowed"),
        ))


if __name__ == "__main__":
    unittest.main()
