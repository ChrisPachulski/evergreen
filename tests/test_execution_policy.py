from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
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


if __name__ == "__main__":
    unittest.main()
