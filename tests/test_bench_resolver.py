import unittest


from eval.bench.resolver import (
    needs_synthesis_v1, needs_synthesis_v2, resolve, resolve_v1, resolve_v2,
    resolve_v3, route_screen_v3,
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


def screen_verdict(value, proof="direct", category=None, uncertain=False,
                    uncertainty_reason=None):
    return {
        "verdict": value, "proof": proof, "category": category,
        "claim": "the documentation claim", "evidence": "return 1",
        "uncertain": uncertain, "uncertainty_reason": uncertainty_reason,
    }


def v1_unanimous(value="consistent"):
    category = "direct-mismatch" if value == "inconsistent" else None
    return {
        "snap": ok(verdict(value, category)),
        "challenge": ok({"cracks": False, "why": "did not land"}),
        "prongs": [ok({"role": role, "verdict": value, "why": "evidence"})
                   for role in ("defend", "prove-wrong", "hardest-broken")],
        "blindspot": ok({"missed_angle": None}),
    }


def v2_unanimous(value="consistent", proof="direct"):
    category = "direct-mismatch" if value == "inconsistent" else None
    roles = ("defend", "prove-wrong", "evidence-auditor")
    return {
        "snap": ok(proof_verdict(value, proof, category)),
        "challenge": ok({"cracks": False, "why": "attack failed"}),
        "prongs": [ok(proof_verdict(value, proof, category, role)) for role in roles],
        "blindspot": ok({"missed_angle": None}),
    }


class RouteScreenV3Tests(unittest.TestCase):
    def test_route_decision_matrix(self):
        missing_evidence = {
            "verdict": "consistent", "proof": "direct", "category": None,
            "claim": "x", "uncertain": False, "uncertainty_reason": None,
        }
        non_bool_uncertain = screen_verdict("consistent")
        non_bool_uncertain["uncertain"] = "yes"
        missing_uncertainty_reason = screen_verdict("consistent")
        del missing_uncertainty_reason["uncertainty_reason"]

        cases = (
            ("direct, category-free, non-uncertain consistent",
             ok(screen_verdict("consistent")), "clear", "direct-consistent"),
            ("inconsistent verdict",
             ok(screen_verdict("inconsistent")), "jury", "inconsistent"),
            ("unverified verdict",
             ok(screen_verdict("unverified")), "jury", "unverified"),
            ("delegated proof",
             ok(screen_verdict("consistent", proof="delegated")),
             "jury", "non-direct-proof"),
            ("requires-unseen-code proof",
             ok(screen_verdict("consistent", proof="requires-unseen-code")),
             "jury", "non-direct-proof"),
            ("category present",
             ok(screen_verdict("consistent", category="under-promise")),
             "jury", "category-present"),
            ("uncertain screen",
             ok(screen_verdict("consistent", uncertain=True, uncertainty_reason="not sure")),
             "jury", "screen-uncertain"),
            ("malformed value: missing evidence",
             ok(missing_evidence), "jury", "screen-invalid-or-abstained"),
            ("malformed value: uncertain is not a bool",
             ok(non_bool_uncertain), "jury", "screen-invalid-or-abstained"),
            ("malformed value: uncertainty_reason absent",
             ok(missing_uncertainty_reason), "jury", "screen-invalid-or-abstained"),
            ("stage abstention",
             {"status": "abstain", "value": None}, "jury", "screen-invalid-or-abstained"),
        )
        for name, result, decision, reason in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    route_screen_v3(result), {"decision": decision, "reason": reason}
                )


class ResolverV3Tests(unittest.TestCase):
    def clear_stages(self, **overrides):
        screen = ok(screen_verdict("consistent"))
        stages = {"screen": screen, "route": route_screen_v3(screen)}
        stages.update(overrides)
        return stages

    def jury_stages(self, nested, screen_value="unverified", **screen_kwargs):
        screen = ok(screen_verdict(screen_value, **screen_kwargs))
        return {"screen": screen, "route": route_screen_v3(screen), "jury": nested}

    def test_clear_route_produces_direct_consistent_decision_with_no_jury(self):
        stages = self.clear_stages()
        result = resolve_v3(stages)
        self.assertEqual(result["final_status"], "complete")
        self.assertEqual(result["semantic_status"], "decided")
        self.assertEqual(result["final_verdict"], "consistent")
        self.assertEqual(result["verdict"], "consistent")
        self.assertIsNone(result["category"])
        self.assertEqual(result["proof"], "direct")
        self.assertFalse(result["contested"])
        self.assertNotIn("jury", stages)

    def test_stored_route_mismatch_is_infrastructure_abstention(self):
        stages = self.clear_stages(route={"decision": "clear", "reason": "wrong-reason"})
        result = resolve_v3(stages)
        self.assertEqual(result["final_status"], "abstain")
        self.assertIsNone(result["final_verdict"])
        self.assertEqual(result["why"], "stored route does not match recomputed route")

    def test_missing_stored_route_is_infrastructure_abstention(self):
        stages = {"screen": ok(screen_verdict("consistent"))}
        result = resolve_v3(stages)
        self.assertEqual(result["final_status"], "abstain")
        self.assertEqual(result["why"], "stored route does not match recomputed route")

    def test_jury_route_reproduces_resolve_v2_exactly(self):
        nested = v2_unanimous()
        stages = self.jury_stages(nested, "consistent", proof="requires-unseen-code")
        expected = resolve_v2(nested)
        result = resolve_v3(stages)
        for key in ("final_status", "semantic_status", "final_verdict", "verdict",
                    "category", "proof", "claim", "evidence", "why"):
            self.assertEqual(result.get(key), expected.get(key), key)

    def test_incomplete_nested_jury_abstains_and_preserves_exact_reason(self):
        nested = v2_unanimous()
        del nested["snap"]["value"]["evidence"]
        stages = self.jury_stages(nested)
        expected = resolve_v2(nested)
        result = resolve_v3(stages)
        self.assertEqual(result["final_status"], "abstain")
        self.assertEqual(result["why"], expected["why"])

    def test_dispatch_accepts_v3_clear(self):
        self.assertEqual(resolve(self.clear_stages(), "v3")["semantic_status"], "decided")

    def test_dispatch_accepts_v3_jury(self):
        nested = v2_unanimous()
        stages = self.jury_stages(nested)
        self.assertEqual(resolve(stages, "v3"), resolve_v2(nested))

    def test_dispatch_rejects_unknown_resolver_mentions_all_three(self):
        with self.assertRaisesRegex(ValueError, "v1, v2, or v3"):
            resolve({}, "unknown")


class ResolverV1V2FixtureStabilityTests(unittest.TestCase):
    """v3's addition to resolve() must not perturb v1/v2 fixture outputs."""

    def test_v1_fixture_output_is_unchanged(self):
        stages = v1_unanimous()
        self.assertEqual(resolve(stages, "v1"), {
            "final_status": "complete", "final_verdict": "consistent",
            "verdict": "consistent", "category": None, "why": "evidence",
            "contested": False, "stages": stages,
        })

    def test_v2_fixture_output_is_unchanged(self):
        stages = v2_unanimous()
        self.assertEqual(resolve(stages, "v2"), {
            "final_status": "complete", "semantic_status": "decided",
            "final_verdict": "consistent", "verdict": "consistent",
            "category": None, "why": "return 1", "proof": "direct",
            "claim": "the documentation claim", "evidence": "return 1",
            "contested": False, "stages": stages,
        })


if __name__ == "__main__":
    unittest.main()
