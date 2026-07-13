import unittest


from eval.bench.resolver import (
    needs_synthesis_v1, needs_synthesis_v2, resolve, resolve_v1, resolve_v2,
)


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
        self.assertEqual(
            result["why"],
            "one or more prong responses are missing required fields",
        )

    def test_dispatch_rejects_unknown_resolver(self):
        self.assertEqual(resolve(self.unanimous(), "v1")["final_verdict"], "consistent")
        with self.assertRaisesRegex(ValueError, "resolver"):
            resolve(self.unanimous(), "unknown")


def proof_verdict(value, proof="direct", category=None, role=None, cleared_bar=True):
    record = {
        "verdict": value, "proof": proof, "category": category,
        "claim": "the documentation claim", "evidence": "return 1",
    }
    if role is not None:
        record["role"] = role
        record["cleared_bar"] = cleared_bar
    return record


class ResolverV2Tests(unittest.TestCase):
    roles = ("defend", "prove-wrong", "evidence-auditor")

    def unanimous(self, value="consistent", proof="direct"):
        category = "direct-mismatch" if value == "inconsistent" else None
        return {
            "snap": ok(proof_verdict(value, proof, category)),
            "challenge": ok({"cracks": False, "why": "attack failed"}),
            "prongs": [ok(proof_verdict(value, proof, category, role))
                       for role in self.roles],
            "blindspot": ok({"missed_angle": None}),
        }

    def test_unanimous_direct_evidence_is_a_semantic_decision(self):
        stages = self.unanimous()
        self.assertFalse(needs_synthesis_v2(stages))
        result = resolve_v2(stages)
        self.assertEqual(result["final_status"], "complete")
        self.assertEqual(result["semantic_status"], "decided")
        self.assertEqual(result["final_verdict"], "consistent")

    def test_non_direct_consensus_forces_synthesis_and_downgrades_to_unverified(self):
        stages = self.unanimous(proof="requires-unseen-code")
        stages["synthesis"] = ok(proof_verdict(
            "consistent", "requires-unseen-code"
        ))
        self.assertTrue(needs_synthesis_v2(stages))
        result = resolve_v2(stages)
        self.assertEqual(result["final_status"], "complete")
        self.assertEqual(result["semantic_status"], "unverified")
        self.assertIsNone(result["final_verdict"])

    def test_inconsistent_requires_direct_proof_and_scored_category(self):
        for proof, category in (
            ("delegated", "direct-mismatch"),
            ("direct", None),
            ("direct", "under-promise"),
        ):
            with self.subTest(proof=proof, category=category):
                stages = self.unanimous()
                stages["synthesis"] = ok(proof_verdict(
                    "inconsistent", proof, category
                ))
                stages["challenge"] = ok({"cracks": True, "why": "landed"})
                result = resolve_v2(stages)
                self.assertEqual(result["semantic_status"], "unverified")
                self.assertIsNone(result["final_verdict"])

    def test_challenge_blindspot_or_genuine_tie_forces_synthesis(self):
        mutations = (
            lambda stages: stages["challenge"]["value"].update(cracks=True),
            lambda stages: stages["blindspot"]["value"].update(missed_angle="edge"),
            lambda stages: [stages["prongs"][index].update(
                value=proof_verdict("inconsistent", category="direct-mismatch",
                                    role=self.roles[index])
            ) for index in (1, 2)],
        )
        for mutate in mutations:
            stages = self.unanimous()
            mutate(stages)
            with self.subTest(stages=stages):
                self.assertTrue(needs_synthesis_v2(stages))

    def test_cleared_bar_plurality_allows_dissent_without_synthesis(self):
        stages = self.unanimous()
        stages["prongs"][1] = ok(proof_verdict(
            "inconsistent", category="direct-mismatch", role="prove-wrong"
        ))
        self.assertFalse(needs_synthesis_v2(stages))
        self.assertEqual(resolve_v2(stages)["final_verdict"], "consistent")

    def test_conceded_opposing_lenses_do_not_count_as_dissent(self):
        stages = self.unanimous()
        for index, role in ((1, "prove-wrong"), (2, "evidence-auditor")):
            stages["prongs"][index] = ok(proof_verdict(
                "inconsistent", category="direct-mismatch", role=role,
                cleared_bar=False,
            ))
        self.assertFalse(needs_synthesis_v2(stages))
        self.assertEqual(resolve_v2(stages)["final_verdict"], "consistent")

    def test_non_direct_evidence_gate_is_separate_from_mere_dissent(self):
        stages = self.unanimous()
        stages["prongs"][1] = ok(proof_verdict(
            "consistent", proof="delegated", role="prove-wrong"
        ))
        self.assertTrue(needs_synthesis_v2(stages))

    def test_semantically_incoherent_vote_category_forces_synthesis(self):
        for value, category in (("consistent", "direct-mismatch"),
                                ("inconsistent", None),
                                ("inconsistent", "under-promise")):
            stages = self.unanimous(value)
            stages["snap"]["value"]["category"] = category
            with self.subTest(value=value, category=category):
                self.assertTrue(needs_synthesis_v2(stages))

    def test_malformed_proof_record_is_infrastructure_abstention(self):
        stages = self.unanimous()
        del stages["snap"]["value"]["evidence"]
        result = resolve_v2(stages)
        self.assertEqual(result["final_status"], "abstain")
        self.assertEqual(result["semantic_status"], "not-evaluated")

    def test_prong_without_cleared_bar_is_infrastructure_abstention(self):
        stages = self.unanimous()
        del stages["prongs"][0]["value"]["cleared_bar"]
        self.assertEqual(resolve_v2(stages)["final_status"], "abstain")

    def test_escalated_prongs_replace_initial_prongs_during_validation(self):
        stages = self.unanimous()
        stages["prongs_escalated"] = [
            ok(proof_verdict("consistent", role=role)) for role in self.roles
        ]
        del stages["prongs_escalated"][0]["value"]["cleared_bar"]
        self.assertEqual(resolve_v2(stages)["final_status"], "abstain")

    def test_dispatch_accepts_v2(self):
        self.assertEqual(resolve(self.unanimous(), "v2")["semantic_status"], "decided")


if __name__ == "__main__":
    unittest.main()
