"""Regression tests for drift evergreen found in its own docs.

Each test ties a doc claim to the code or file that settles it, so the claim cannot
silently drift back.
"""
import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
FLOURISH_FIXTURE = ROOT / "eval" / "flourish" / "fixtures" / "voiced"


def read(*parts):
    return (ROOT.joinpath(*parts)).read_text(encoding="utf-8")


def load_flourish_score():
    spec = importlib.util.spec_from_file_location(
        "flourish_score", ROOT / "eval" / "flourish" / "score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DocDriftTests(unittest.TestCase):
    def test_agent_run_instructions_never_call_bin_evergreen_by_a_relative_path(self):
        """`bin/evergreen` exists only inside the evergreen checkout; a consumer repo has
        none, so a command or skill telling the agent to run it relatively exits 127."""
        surfaces = sorted((ROOT / "commands").glob("*.md")) + [
            ROOT / "skills" / "evergreen" / "SKILL.md",
            ROOT / "skills" / "evergreen" / "DIGEST.md",
        ]
        relative = re.compile(r'(?<![/\w"])bin/evergreen\s+\w+\s+--')
        for surface in surfaces:
            with self.subTest(surface=surface.name):
                self.assertIsNone(relative.search(surface.read_text(encoding="utf-8")))

    def test_readme_does_not_say_a_flattened_hook_trips_the_scorer(self):
        result = subprocess.run(
            [sys.executable, "-I", str(ROOT / "eval" / "flourish" / "score.py"),
             "--fixture", str(FLOURISH_FIXTURE),
             "--result", str(FLOURISH_FIXTURE / "golden" / "flattened.md"),
             "--report", str(FLOURISH_FIXTURE / "golden" / "flattened.report.md")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout)  # voice never gates
        readme = " ".join(read("README.md").split())
        sentence = readme.split("flattened hook", 1)[1].split(". ", 1)[0]
        self.assertNotIn("trip", sentence)
        self.assertIn("advisory", sentence)

    def test_flourish_face_gate_mermaid_claim_matches_the_scorer(self):
        doc = (
            '<h1 align="center">Tool</h1>\n\n'
            '<p align="center"><em>A tagline with a point of view.</em></p>\n\n'
            "Tool is a thing that does stuff for you every day.\n\n"
            "## How it works\n\n"
            '```mermaid\ngraph TD\n    a["in"] --> b["out"]\n```\n'
        )
        result, _ = load_flourish_score().gate_face(doc)
        counted = result["checks"]["visual"]["ok"]
        command = " ".join(read("commands", "flourish.md").split())
        self.assertEqual("mermaid fence is not counted" in command, not counted)

    def test_readme_rerun_plan_link_points_at_a_file_with_the_rerun_plan(self):
        readme = read("README.md")
        match = re.search(r"rerun (?:plan|condition)[^:]*: \[`([^`]+)`\]", readme)
        self.assertIsNotNone(match, "README must link the benchmark rerun plan")
        self.assertIn("rerun", read(match.group(1)).lower())

    def test_winnow_finding_line_carries_the_rung_the_envelope_requires(self):
        self.assertIn('"rung"', read("ci", "result_protocol.py"))
        winnow = " ".join(read("commands", "winnow.md").split())
        self.assertIn("[sev] category rung file:line", winnow)
        self.assertIn("path · contract · snippet · prose", winnow)


if __name__ == "__main__":
    unittest.main()
