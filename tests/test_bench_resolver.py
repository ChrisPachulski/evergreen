import unittest


from eval.bench.resolver import needs_synthesis_v1, resolve, resolve_v1


def ok(value):
    return {"status": "ok", "value": value}


def verdict(value, category=None, why="evidence"):
    return {"verdict": value, "category": category, "why": why}


class ResolverV1Tests(unittest.TestCase):
    def unanimous(self, value="consistent"):
        category = "direct-mismatch" if value == "inconsistent" else None
        return {
            "snap": ok(verdict(value, category)),
            "challenge": ok({"cracks": False, "why": "did not land"}),
            "prongs": [ok({"role": role, "verdict": value, "why": "evidence"})
                       for role in ("defend", "prove-wrong", "hardest-broken")],
            "blindspot": ok({"missed_angle": None}),
        }

    def test_unanimous_record_uses_snap_without_synthesis(self):
        stages = self.unanimous()
        self.assertFalse(needs_synthesis_v1(stages))
        result = resolve_v1(stages)
        self.assertEqual(result["final_status"], "complete")
        self.assertEqual(result["final_verdict"], "consistent")
        self.assertFalse(result["contested"])

    def test_vote_disagreement_uses_stored_synthesis(self):
        stages = self.unanimous()
        stages["prongs"][1] = ok({"role": "prove-wrong", "verdict": "inconsistent"})
        stages["synthesis"] = ok(verdict(
            "inconsistent", "direct-mismatch", "doc says one; code returns two"
        ))
        self.assertTrue(needs_synthesis_v1(stages))
        result = resolve_v1(stages)
        self.assertEqual(result["final_verdict"], "inconsistent")
        self.assertEqual(result["category"], "direct-mismatch")

    def test_blindspot_requires_synthesis(self):
        stages = self.unanimous()
        stages["blindspot"] = ok({"missed_angle": "direct mismatch"})
        stages["synthesis"] = ok(verdict("consistent"))
        self.assertTrue(needs_synthesis_v1(stages))
        self.assertTrue(resolve_v1(stages)["contested"])

    def test_escalated_prongs_replace_initial_prongs_and_mark_contested(self):
        stages = self.unanimous("inconsistent")
        stages["prongs"] = [
            ok({"role": "defend", "verdict": "consistent"}),
            ok({"role": "prove-wrong", "verdict": "consistent"}),
            ok({"role": "hardest-broken", "verdict": "inconsistent"}),
        ]
        stages["prongs_escalated"] = [
            ok({"role": role, "verdict": "inconsistent"})
            for role in ("defend", "prove-wrong", "hardest-broken")
        ]
        result = resolve_v1(stages)
        self.assertEqual(result["final_verdict"], "inconsistent")
        self.assertTrue(result["contested"])

    def test_missing_required_synthesis_abstains(self):
        stages = self.unanimous()
        stages["prongs"][0] = ok({"role": "defend", "verdict": "inconsistent"})
        result = resolve_v1(stages)
        self.assertEqual(result["final_status"], "abstain")
        self.assertIsNone(result["final_verdict"])
        self.assertIn("synthesis", result["why"])

    def test_malformed_stage_abstains(self):
        stages = self.unanimous()
        stages["prongs"][1] = ok({"role": "prove-wrong"})
        result = resolve_v1(stages)
        self.assertEqual(result["final_status"], "abstain")
        self.assertIn("trial record", result["why"])

    def test_dispatch_rejects_unknown_resolver(self):
        self.assertEqual(resolve(self.unanimous(), "v1")["final_verdict"], "consistent")
        with self.assertRaisesRegex(ValueError, "resolver"):
            resolve(self.unanimous(), "unknown")


if __name__ == "__main__":
    unittest.main()
