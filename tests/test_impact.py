import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evergreen.evidence import Evidence


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write_map(self, maps, **root_changes):
        value = {"version": 1, "maps": maps}
        value.update(root_changes)
        (self.repo / ".evergreen-map.json").write_text(json.dumps(value))

    def mapping(self, sources=None, docs=None):
        return {
            "sources": sources or ["src/public-api/**"],
            "docs": docs or ["docs/api.md"],
        }

    def evidence(self, **changes):
        value = {
            "provider": "drift-shape", "version": "1.0", "type": "export-removed",
            "path": "src/public-api/client.py", "line": 8, "span": 1,
            "symbol": "Client", "old": "exported", "current": "missing",
            "confidence": "deterministic", "metadata": {},
        }
        value.update(changes)
        return Evidence(**value)

    def test_mapped_and_unmapped_changed_paths_are_additive_candidates(self):
        from evergreen.impact import impact

        self.write_map([self.mapping()])
        report = impact(self.repo, ["other.py", "src/public-api/client.py"], [])

        self.assertEqual(report.warnings, ())
        self.assertEqual(
            [(candidate.path, candidate.rank) for candidate in report.candidates],
            [("docs/api.md", 100), ("other.py", 10), ("src/public-api/client.py", 10)],
        )
        self.assertIn("map src/public-api/** matched src/public-api/client.py",
                      report.candidates[0].reasons)
        self.assertTrue(all(not hasattr(candidate, "verdict") for candidate in report.candidates))

    def test_zero_config_discovers_tracked_living_docs_by_path_and_contract_symbol(self):
        from evergreen.impact import impact

        (self.repo / "src/public-api").mkdir(parents=True)
        (self.repo / "src/public-api/client.py").write_text(
            "class Client:\n    pass\n", encoding="utf-8"
        )
        (self.repo / "docs").mkdir()
        (self.repo / "docs/usage.md").write_text(
            "Implementation: `src/public-api/client.py`.\n", encoding="utf-8"
        )
        (self.repo / "docs/api.md").write_text(
            "Construct a `Client` for each service.\n", encoding="utf-8"
        )
        (self.repo / "docs/2026-07-12-audit.md").write_text(
            "Snapshot of Client in src/public-api/client.py.\n", encoding="utf-8"
        )
        (self.repo / "docs/superpowers/specs").mkdir(parents=True)
        (self.repo / "docs/superpowers/specs/history.md").write_text(
            "Historical Client plan for src/public-api/client.py.\n", encoding="utf-8"
        )
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)

        report = impact(self.repo, ["src/public-api/client.py"], [])

        self.assertEqual(
            [(candidate.path, candidate.rank) for candidate in report.candidates],
            [
                ("docs/api.md", 80),
                ("docs/usage.md", 80),
                ("src/public-api/client.py", 10),
            ],
        )
        self.assertIn("contract symbol Client", report.candidates[0].reasons[0])
        self.assertIn("changed path src/public-api/client.py", report.candidates[1].reasons[0])

    def test_zero_config_doc_search_has_a_deterministic_file_bound(self):
        from evergreen import impact as module

        (self.repo / "src").mkdir()
        (self.repo / "src/client.py").write_text("class Client:\n    pass\n")
        (self.repo / "docs").mkdir()
        for name in ("a.md", "b.md"):
            (self.repo / "docs" / name).write_text("Client\n")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)

        with mock.patch.object(module, "MAX_DOC_SEARCH_FILES", 1):
            report = module.impact(self.repo, ["src/client.py"], [])

        self.assertEqual([item.path for item in report.candidates], [
            "docs/a.md", "src/client.py",
        ])
        self.assertTrue(any("living docs truncated" in item for item in report.warnings))

    def test_zero_config_doc_search_has_a_deterministic_byte_bound(self):
        from evergreen import impact as module

        (self.repo / "src").mkdir()
        (self.repo / "src/client.py").write_text("class Client:\n    pass\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs/api.md").write_text("Client\n")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)

        with mock.patch.object(module, "MAX_DOC_SEARCH_BYTES", 1):
            report = module.impact(self.repo, ["src/client.py"], [])

        self.assertEqual([item.path for item in report.candidates], ["src/client.py"])
        self.assertTrue(any("living doc search truncated" in item for item in report.warnings))

    def test_multiple_maps_merge_reasons_dedupe_and_sort_deterministically(self):
        from evergreen.impact import impact, load_map

        maps = [
            self.mapping(["src/**"], ["README.md", "docs/api.md"]),
            self.mapping(["src/public-api/**"], ["docs/api.md"]),
        ]
        self.write_map(list(reversed(maps)) + [maps[0]])
        loaded, warnings = load_map(self.repo)
        first = impact(self.repo, ["src/public-api/client.py"], [])
        second = impact(self.repo, ["src/public-api/client.py"], [])

        self.assertEqual(len(loaded), 2)
        self.assertEqual(len(warnings), 1)
        self.assertIn("duplicate", warnings[0])
        self.assertEqual(first, second)
        self.assertEqual([candidate.path for candidate in first.candidates], [
            "docs/api.md", "README.md", "src/public-api/client.py",
        ])
        self.assertEqual(len(first.candidates[0].reasons), 2)

    def test_invalid_patterns_and_records_warn_while_valid_maps_survive(self):
        from evergreen.impact import load_map

        self.write_map([
            self.mapping(["../escape/**"]),
            self.mapping(["src/[broken"]),
            self.mapping(["src/**"], ["../outside.md"]),
            self.mapping(["src/**"], ["docs/source.md"]),
            {"sources": "src/**", "docs": []},
            self.mapping(["src/**", "src/**"], ["docs/duplicate.md"]),
        ])
        maps, warnings = load_map(self.repo)

        self.assertEqual(len(maps), 1)
        self.assertEqual(maps[0].docs, ("docs/source.md",))
        self.assertEqual(len(warnings), 5)

    def test_valid_fnmatch_character_classes_are_accepted(self):
        from evergreen.impact import load_map

        self.write_map([
            self.mapping(["src/[!a]*.py"], ["docs/not-a.md"]),
            self.mapping(["src/[[]generated.py"], ["docs/generated.md"]),
        ])
        maps, warnings = load_map(self.repo)

        self.assertEqual(len(maps), 2)
        self.assertEqual(warnings, [])

    def test_unbalanced_closing_bracket_rejects_only_its_map(self):
        from evergreen.impact import load_map

        self.write_map([
            self.mapping(["src/file].py"], ["docs/bad.md"]),
            self.mapping(["src/[a/file].py"], ["docs/cross-segment.md"]),
            self.mapping(["src/file.py"], ["docs/good.md"]),
        ])
        maps, warnings = load_map(self.repo)

        self.assertEqual([mapping.docs for mapping in maps], [("docs/good.md",)])
        self.assertEqual(warnings, [
            "map 1: source pattern has invalid brackets",
            "map 2: source pattern has invalid brackets",
        ])

    def test_segment_aware_globs_keep_star_shallow_and_globstar_recursive(self):
        from evergreen.impact import impact

        self.write_map([
            self.mapping(["src/*.py"], ["docs/shallow.md"]),
            self.mapping(["src/**"], ["docs/recursive.md"]),
            self.mapping(["src/**/client.py"], ["docs/client.md"]),
        ])
        nested = impact(self.repo, ["src/nested/api.py"], [])
        direct = impact(self.repo, ["src/client.py"], [])

        nested_paths = [candidate.path for candidate in nested.candidates]
        direct_paths = [candidate.path for candidate in direct.candidates]
        self.assertNotIn("docs/shallow.md", nested_paths)
        self.assertIn("docs/recursive.md", nested_paths)
        self.assertIn("docs/shallow.md", direct_paths)
        self.assertIn("docs/client.md", direct_paths)

    def test_over_segment_glob_is_rejected_before_matching_without_suppressing_baseline(self):
        from evergreen import impact as module

        hostile_pattern = "/".join(["**", "a"] * 800)
        self.write_map([self.mapping([hostile_pattern], ["docs/hostile.md"])])
        report = module.impact(self.repo, ["src/a.py"], [])

        self.assertEqual([candidate.path for candidate in report.candidates], ["src/a.py"])
        self.assertTrue(any("source pattern has too many segments" in warning
                            for warning in report.warnings))
        self.assertFalse(any("matching work truncated" in warning
                             for warning in report.warnings))

    def test_over_segment_changed_path_warns_while_valid_baseline_survives(self):
        from evergreen.impact import impact

        hostile_path = "/".join(["a"] * 65)
        boundary_path = "/".join(["b"] * 64)
        report = impact(self.repo, [hostile_path, boundary_path, "valid.py"], [])

        self.assertEqual({candidate.path for candidate in report.candidates}, {
            boundary_path, "valid.py",
        })
        self.assertTrue(any("changed path has too many segments" in warning
                            for warning in report.warnings))

    def test_deleted_source_and_missing_doc_targets_are_valid_candidates(self):
        from evergreen.impact import impact

        self.write_map([self.mapping(["deleted/**"], ["docs/deleted-api.md"])])
        report = impact(self.repo, ["deleted/old.py"], [])

        self.assertEqual([candidate.path for candidate in report.candidates], [
            "docs/deleted-api.md", "deleted/old.py",
        ])

    def test_provider_evidence_expands_source_and_mapped_doc_candidates_only(self):
        from evergreen.impact import impact

        self.write_map([self.mapping()])
        report = impact(self.repo, [], [self.evidence()])

        self.assertEqual([(candidate.path, candidate.rank) for candidate in report.candidates], [
            ("docs/api.md", 100), ("src/public-api/client.py", 50),
        ])
        self.assertIn("evidence drift-shape@1.0 export-removed (deterministic)",
                      report.candidates[1].reasons)
        self.assertFalse(any(word in reason.lower() for candidate in report.candidates
                             for reason in candidate.reasons for word in ("finding", "verdict")))

    def test_deterministic_fixture_remains_a_candidate_without_semantic_verdict(self):
        from evergreen.evidence import load_evidence
        from evergreen.impact import impact

        root = Path(__file__).resolve().parents[1]
        evidence, warnings = load_evidence(root / "examples/provider-evidence.json", root)
        timeout = next(item for item in evidence if item.type == "constant-value-changed")
        return_contract = next(item for item in evidence
                               if item.type == "return-contract-changed")

        self.assertEqual(warnings, [])
        self.assertEqual(return_contract.line, 11)
        report = impact(root, [], [timeout])
        self.assertEqual([candidate.path for candidate in report.candidates], [
            "eval/fixture/config.py",
        ])
        self.assertIn("(deterministic)", report.candidates[0].reasons[0])
        self.assertFalse(hasattr(report.candidates[0], "finding"))
        self.assertFalse(hasattr(report.candidates[0], "verdict"))

        boundary = (root / "examples/provider-boundary.md").read_text()
        self.assertIn("Expected: no finding", boundary)
        self.assertIn("per-project timeout override remains true", boundary)

    def test_impact_revalidates_hostile_and_malformed_evidence_without_aborting(self):
        from evergreen.impact import impact

        hostile = [
            object(),
            self.evidence(provider="bad\nprovider"),
            self.evidence(confidence="certain"),
            self.evidence(line="8"),
            self.evidence(path="bad\npath.py"),
            self.evidence(provider="valid", path="valid.py"),
        ]
        report = impact(self.repo, [], hostile)

        self.assertEqual([candidate.path for candidate in report.candidates], ["valid.py"])
        self.assertEqual(len(report.warnings), 5)
        self.assertTrue(all("evidence" in warning for warning in report.warnings))

    def test_input_and_evidence_order_do_not_change_report_order(self):
        from evergreen.impact import impact

        self.write_map([])
        evidence = [self.evidence(path="z.py"), self.evidence(path="a.py", provider="other")]
        first = impact(self.repo, ["m.py", "b.py"], evidence)
        second = impact(self.repo, ["b.py", "m.py"], list(reversed(evidence)))

        self.assertEqual(first, second)
        self.assertEqual([candidate.path for candidate in first.candidates], [
            "a.py", "z.py", "b.py", "m.py",
        ])

    def test_missing_or_hostile_config_never_suppresses_normal_candidates(self):
        from evergreen import impact as module

        missing = module.impact(self.repo, ["src/a.py"], [])
        self.assertEqual([candidate.path for candidate in missing.candidates], ["src/a.py"])
        self.assertEqual(missing.warnings, ())

        config = self.repo / ".evergreen-map.json"
        config.write_text("not json")
        malformed = module.impact(self.repo, ["src/a.py"], [])
        self.assertEqual([candidate.path for candidate in malformed.candidates], ["src/a.py"])
        self.assertTrue(malformed.warnings)

        config.write_text("{}" + " " * 20)
        with mock.patch.object(module, "MAX_FILE_BYTES", 4):
            maps, warnings = module.load_map(self.repo)
        self.assertEqual(maps, [])
        self.assertTrue(any("too large" in warning for warning in warnings))

    def test_collection_work_output_and_reason_bounds_warn_deterministically(self):
        from evergreen import impact as module

        self.write_map([
            self.mapping(["src/**", "src/*.py"], ["docs/a.md", "docs/b.md", "docs/c.md"]),
        ])
        evidence = [self.evidence(path="z.py"), self.evidence(path="a.py", provider="other")]
        with mock.patch.object(module, "MAX_CHANGED_PATHS", 2), \
             mock.patch.object(module, "MAX_EVIDENCE_ITEMS", 1), \
             mock.patch.object(module, "MAX_CANDIDATES", 2), \
             mock.patch.object(module, "MAX_REASONS_PER_CANDIDATE", 1):
            first = module.impact(self.repo, ["src/z.py", "src/a.py", "src/m.py"], evidence)
            second = module.impact(
                self.repo, ["src/z.py", "src/a.py", "src/ignored.py"], evidence
            )

        self.assertEqual(first, second)
        self.assertLessEqual(len(first.candidates), 2)
        self.assertTrue(all(len(candidate.reasons) <= 1 for candidate in first.candidates))
        self.assertTrue(any("changed paths truncated" in warning for warning in first.warnings))
        self.assertTrue(any("evidence items truncated" in warning for warning in first.warnings))
        self.assertTrue(any("candidates truncated" in warning for warning in first.warnings))

        with mock.patch.object(module, "MAX_MATCH_WORK", 1):
            bounded = module.impact(self.repo, ["src/a.py", "src/b.py"], [])
        self.assertTrue(any("matching work truncated" in warning for warning in bounded.warnings))

    def test_candidate_cap_reserves_changed_path_before_additive_candidates(self):
        from evergreen import impact as module

        self.write_map([self.mapping(["src/**"], ["docs/a.md"])])
        with mock.patch.object(module, "MAX_CANDIDATES", 1):
            report = module.impact(
                self.repo, ["src/changed.py"], [self.evidence(path="src/evidence.py")]
            )

        self.assertEqual([candidate.path for candidate in report.candidates], [
            "src/changed.py",
        ])
        self.assertTrue(any("candidates truncated" in warning for warning in report.warnings))

    def test_impact_rejects_non_concrete_inputs_and_slices_before_validation(self):
        from evergreen import impact as module

        class HostileIterable:
            def __iter__(self):
                raise AssertionError("must not consume hostile iterable")

        class HostileList(list):
            def __len__(self):
                raise AssertionError("must not inspect hostile sequence subclass")

        rejected = module.impact(self.repo, HostileIterable(), HostileIterable())
        self.assertEqual(rejected.candidates, ())
        self.assertTrue(any("changed paths must be" in warning for warning in rejected.warnings))
        self.assertTrue(any("evidence must be" in warning for warning in rejected.warnings))

        subclassed = module.impact(self.repo, HostileList(), HostileList())
        self.assertEqual(subclassed.candidates, ())

        with mock.patch.object(module, "MAX_CHANGED_PATHS", 1), \
             mock.patch.object(module, "MAX_EVIDENCE_ITEMS", 1):
            bounded = module.impact(
                self.repo,
                ["kept.py", object()],
                [self.evidence(path="evidence.py"), object()],
            )
        self.assertEqual([candidate.path for candidate in bounded.candidates], [
            "evidence.py", "kept.py",
        ])
        self.assertFalse(any("invalid type" in warning for warning in bounded.warnings))
        self.assertTrue(any("changed paths truncated" in warning for warning in bounded.warnings))
        self.assertTrue(any("evidence items truncated" in warning for warning in bounded.warnings))

    def test_over_limit_inputs_retain_caller_order_prefix(self):
        from evergreen import impact as module

        self.assertIn("caller-order prefix", module.impact.__doc__)
        self.assertIn("never suppress", module.impact.__doc__)

        with mock.patch.object(module, "MAX_CHANGED_PATHS", 1), \
             mock.patch.object(module, "MAX_EVIDENCE_ITEMS", 1):
            first = module.impact(
                self.repo,
                ["first.py", "second.py"],
                [self.evidence(path="first-evidence.py"),
                 self.evidence(path="second-evidence.py")],
            )
            reversed_input = module.impact(
                self.repo,
                ["second.py", "first.py"],
                [self.evidence(path="second-evidence.py"),
                 self.evidence(path="first-evidence.py")],
            )

        self.assertEqual({candidate.path for candidate in first.candidates}, {
            "first.py", "first-evidence.py",
        })
        self.assertEqual({candidate.path for candidate in reversed_input.candidates}, {
            "second.py", "second-evidence.py",
        })

    def test_mapped_doc_additions_consume_matching_work_budget(self):
        from evergreen import impact as module

        self.write_map([self.mapping(["src/**"], ["docs/a.md", "docs/b.md", "docs/c.md"])])
        with mock.patch.object(module, "MAX_MATCH_WORK", 2):
            transition_bounded = module.impact(self.repo, ["src/a.py"], [])
        self.assertEqual([candidate.path for candidate in transition_bounded.candidates], [
            "src/a.py",
        ])
        self.assertTrue(any("matching work truncated" in warning
                            for warning in transition_bounded.warnings))

        with mock.patch.object(module, "MAX_MATCH_WORK", 4):
            report = module.impact(self.repo, ["src/a.py"], [])

        self.assertEqual([candidate.path for candidate in report.candidates], [
            "docs/a.md", "src/a.py",
        ])
        self.assertTrue(any("matching work truncated" in warning for warning in report.warnings))

    def test_total_reason_budget_gives_each_retained_candidate_one_first(self):
        from evergreen import impact as module

        self.write_map([
            self.mapping(["src/**", "src/*.py"], ["docs/a.md", "docs/b.md"]),
        ])
        with mock.patch.object(module, "MAX_CANDIDATES", 3), \
             mock.patch.object(module, "MAX_TOTAL_REASONS", 3):
            report = module.impact(self.repo, ["src/a.py"], [])

        self.assertEqual(len(report.candidates), 3)
        self.assertTrue(all(len(candidate.reasons) == 1 for candidate in report.candidates))
        self.assertTrue(any("reasons truncated" in warning for warning in report.warnings))

    def test_duplicate_keys_reject_only_the_containing_map(self):
        from evergreen.impact import load_map

        valid = json.dumps(self.mapping(["src/valid/**"], ["docs/valid.md"]))
        duplicate = (
            '{"sources":["src/bad/**"],"sources":["src/other/**"],'
            '"docs":["docs/bad.md"]}'
        )
        (self.repo / ".evergreen-map.json").write_text(
            '{"version":1,"maps":[' + duplicate + "," + valid + "]}"
        )
        maps, warnings = load_map(self.repo)

        self.assertEqual(len(maps), 1)
        self.assertEqual(maps[0].docs, ("docs/valid.md",))
        self.assertEqual(warnings, ["map 1: duplicate JSON key: sources"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO test requires POSIX")
    def test_map_loader_rejects_non_regular_and_symlink_config_without_blocking(self):
        from evergreen.impact import load_map

        config = self.repo / ".evergreen-map.json"
        os.mkfifo(config)
        maps, warnings = load_map(self.repo)
        self.assertEqual(maps, [])
        self.assertEqual(warnings, ["map config must be a regular file"])

    def test_schema_and_example_match_candidate_only_map_contract(self):
        root = Path(__file__).parents[1]
        schema = json.loads((root / "schemas/evergreen-map-v1.schema.json").read_text())
        example = json.loads((root / "examples/evergreen-map.json").read_text())

        self.assertEqual(schema["properties"]["version"]["const"], 1)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(example["version"], 1)
        self.assertTrue(example["maps"])
        self.assertIn("candidate", schema["description"].lower())
        self.assertNotIn("commands", schema["properties"])
        self.assertEqual(schema["x-duplicateKeyBehavior"], "reject-file")
        self.assertEqual(schema["x-impactInputBounds"], {
            "changedPaths": {
                "maxItems": 10000,
                "maxPathSegments": 64,
                "overLimit": "retain-caller-order-prefix",
            },
            "evidence": {
                "maxItems": 10000,
                "maxPathSegments": 64,
                "overLimit": "retain-caller-order-prefix",
            },
        })
        source = schema["properties"]["maps"]["items"]["properties"]["sources"]["items"]
        self.assertEqual(source["x-globSemantics"], "segment-aware")
        self.assertTrue(source["x-runtimeConstraints"]["balancedBracketClasses"])
        self.assertEqual(source["x-runtimeConstraints"]["maxSegments"], 64)
        self.assertIn("**", source["description"])
        self.assertIn("pattern", source)
        docs = schema["properties"]["maps"]["items"]["properties"]["docs"]["items"]
        self.assertEqual(docs["x-maxPathSegments"], 64)
        self.assertIn("pattern", docs)
        self.assertIn("repository-relative", docs["description"])
        self.assertEqual(
            schema["properties"]["maps"]["items"]["x-duplicateKeyBehavior"],
            "reject-containing-map",
        )


if __name__ == "__main__":
    unittest.main()
