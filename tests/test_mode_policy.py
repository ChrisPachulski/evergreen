import unittest

from evergreen.mode_policy import (
    LADDER, MODE_RUNGS, declared_mode, emitted_rungs, violations,
)


LIGHT_CLEAN = """evergreen [light]: you renamed `--workers` to `--concurrency`.
  [high] in_docs_not_code contract  README.md:42 - documents `--workers`; gone from cli.py:30 -> fix
  [med]  in_docs_not_code path      docs/cli.md:8 - cites config/legacy.json, gone -> fix
docs otherwise match the code.
"""

LIGHT_VIOLATING = """evergreen [light]: you changed the retry path.
  [med] in_docs_not_code prose  docs/api.md:8 - claims retry-on-timeout; handler retries 5xx -> flag
"""


class ModePolicyTests(unittest.TestCase):
    def test_light_withholds_only_the_judgment_rung(self):
        self.assertEqual(MODE_RUNGS["light"], frozenset(LADDER) - {"prose"})
        self.assertEqual(MODE_RUNGS["strict"], frozenset(LADDER))
        self.assertEqual(MODE_RUNGS["off"], frozenset())

    def test_conforming_light_output_reports_nothing(self):
        self.assertEqual(declared_mode(LIGHT_CLEAN), "light")
        self.assertEqual(
            [rung for _line, rung in emitted_rungs(LIGHT_CLEAN)], ["contract", "path"]
        )
        self.assertEqual(violations(LIGHT_CLEAN), [])

    def test_a_prose_finding_under_light_is_a_violation(self):
        found = violations(LIGHT_VIOLATING)
        self.assertEqual(len(found), 1, found)
        self.assertIn("line 2", found[0])
        self.assertIn("prose", found[0])

    def test_the_same_findings_conform_under_strict(self):
        self.assertEqual(violations(LIGHT_VIOLATING, mode="strict"), [])

    def test_off_may_emit_no_findings_at_all(self):
        self.assertEqual(len(violations(LIGHT_CLEAN, mode="off")), 2)

    def test_an_undeclared_mode_is_itself_a_violation(self):
        """Unfalsifiable is the state this module exists to remove."""
        found = violations("evergreen: docs still match\n")
        self.assertEqual(len(found), 1)
        self.assertIn("no intensity declared", found[0])

    def test_an_unknown_rung_is_reported_rather_than_ignored(self):
        found = violations("evergreen [strict]: x\n  [high] in_docs_not_code vibes  a.md:1 - y -> fix\n")
        self.assertEqual(len(found), 1)
        self.assertIn("unknown rung", found[0])

    def test_an_unknown_mode_is_refused(self):
        self.assertIn("unknown intensity", violations("x", mode="turbo")[0])

    def test_the_documented_rungs_match_the_policy(self):
        """The skill and the code must not drift on what light is allowed to emit."""
        import pathlib

        skill = pathlib.Path(__file__).resolve().parents[1] / "skills/evergreen/SKILL.md"
        text = skill.read_text(encoding="utf-8")
        self.assertIn("light must never emit a `prose` finding", text)
        for rung in LADDER:
            self.assertIn(rung, text, rung)


if __name__ == "__main__":
    unittest.main()
