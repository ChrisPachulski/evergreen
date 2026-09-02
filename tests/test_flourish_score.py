"""Tests for eval/flourish/score.py — synthetic fixtures built in tmp_path.

Run: python3 -m pytest tests/test_flourish_score.py -q
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCORER = Path(__file__).resolve().parents[1] / "eval" / "flourish" / "score.py"

SOURCE = """# shipit

Ships anything, apologizes to no one.

## Architecture

Workers pull jobs from a queue.

## API Reference

`ship(pkg)` sends a package to the queue.

## Configuration

Set SHIPIT_TOKEN in the environment.

## Roadmap

- teleportation
"""

HERO_BLOCK = """<h1 align="center">shipit</h1>
<p align="center"><em>Ships anything, apologizes to no one.</em></p>
<p align="center"><img src="https://img.shields.io/badge/build-passing-green" alt="build"></p>
"""

OPENER = "Tired of hand-rolled deploy scripts? shipit sends a package to the queue with one call.\n"

QUICK_START = """## Quick start

Set SHIPIT_TOKEN in the environment, then call `ship(pkg)`.

```mermaid
flowchart TD
    you[you] --> queue[queue]
    queue --> worker[worker]
```
"""

ARCHITECTURE = """## Architecture

Workers pull jobs from a queue.
"""

API_REFERENCE = """## API Reference

`ship(pkg)` sends a package to the queue.
"""

CONFIGURATION = """## Configuration

Set SHIPIT_TOKEN in the environment.
"""

ROADMAP = """## Roadmap

- teleportation
"""

GOOD = "\n".join([HERO_BLOCK, OPENER, QUICK_START, ARCHITECTURE,
                  API_REFERENCE, CONFIGURATION, ROADMAP])


def ground_truth(**overrides):
    gt = {
        "schema_version": 1,
        "readme": "README.md",
        "sections": [
            {"heading": "## Architecture", "only_home": False},
            {"heading": "## API Reference", "only_home": True},
            {"heading": "## Configuration", "only_home": False},
            {"heading": "## Roadmap", "only_home": False},
        ],
        "claims": [
            {"id": "c1", "text": "ship(pkg) sends a package to the queue",
             "backed": True, "evidence": "cli.py:12"},
            {"id": "c2", "text": "supports encrypted transport over TLS",
             "backed": False, "evidence": None},
        ],
        "voice": {"has_hook": True,
                  "hook_line": "Ships anything, apologizes to no one."},
        "traps": [
            {"kind": "only-home-section",
             "detail": "API Reference exists nowhere else; a cut destroys it"},
            {"kind": "near-backable-claim",
             "detail": "TLS transport sounds plausible but is not implemented"},
            {"kind": "voiced-source",
             "detail": "the apology hook gets flattened to corporate voice"},
        ],
    }
    gt.update(overrides)
    return gt


def make_fixture(tmp_path, gt=None, source=SOURCE):
    fixture = tmp_path / "fixture"
    fixture.mkdir()
    (fixture / "README.md").write_text(source)
    (fixture / "ground-truth.json").write_text(json.dumps(gt or ground_truth()))
    return fixture


def run_scorer(fixture, result_text, tmp_path, cuts=None, as_json=True,
               report=None):
    result = tmp_path / "RESULT.md"
    result.write_text(result_text)
    cmd = [sys.executable, str(SCORER),
           "--fixture", str(fixture), "--result", str(result)]
    if report is not None:
        report_path = tmp_path / "REPORT.md"
        report_path.write_text(report)
        cmd += ["--report", str(report_path)]
    if cuts is not None:
        cuts_path = tmp_path / "cuts.json"
        cuts_path.write_text(json.dumps(cuts))
        cmd += ["--cuts", str(cuts_path)]
    if as_json:
        cmd.append("--json")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    doc = json.loads(proc.stdout) if as_json and proc.returncode != 1 else None
    return proc.returncode, doc, proc


def sections_by_heading(doc):
    return {row["heading"]: row for row in doc["conservation"]["sections"]}



DENSE_BODY = (
    "The scheduler batches outbound requests, deduplicates retried deliveries,\n"
    "compresses payload envelopes, rotates signing credentials nightly, and\n"
    "records latency percentiles for every downstream endpoint it touches.\n")

def dense_fixture(tmp_path):
    gt = ground_truth()
    gt["sections"] = gt["sections"] + [{"heading": "## Deep dive",
                                        "only_home": False}]
    source = SOURCE + "\n## Deep dive\n\n" + DENSE_BODY
    return make_fixture(tmp_path, gt=gt, source=source)

def keyworded_ground_truth():
    """backed=false claim in the new schema: capability in text, annotation in
    note, forbidden-capability stems in keywords."""
    gt = ground_truth()
    gt["claims"][1] = {
        "id": "c2", "text": "supports encrypted transport over TLS",
        "backed": False,
        "note": "TEMPTATION — transport is plaintext; there is no TLS anywhere",
        "keywords": ["tls", "encrypt", "cipher"],
        "evidence": None,
    }
    return gt

class FlourishScoreTests(unittest.TestCase):
    """Unittest-native so CI's `unittest discover` actually runs this file."""

    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_path, True)

    # --- conservation -----------------------------------------------------------

    def test_conservation_pass(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        code, doc, _ = run_scorer(fixture, GOOD, tmp_path)
        assert code == 0
        assert doc["gates"] == {"conservation": True, "truth": True, "face": True,
                                "quick_start": True, "length": True, "diagram": True}
        assert doc["report"]["supplied"] is False
        assert doc["verdict"] == "PASS"
        assert doc["conservation"]["accounted"] == 4
        assert all(r["disposition"] == "present"
                   for r in doc["conservation"]["sections"])


    def test_conservation_gutted(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        gutted = "\n".join([HERO_BLOCK, OPENER, QUICK_START, ARCHITECTURE,
                            CONFIGURATION])  # API Reference and Roadmap deleted
        code, doc, _ = run_scorer(fixture, gutted, tmp_path)
        assert code == 2
        assert doc["gates"]["conservation"] is False
        rows = sections_by_heading(doc)
        assert rows["## API Reference"]["disposition"] == "missing"
        assert rows["## Roadmap"]["disposition"] == "missing"
        assert doc["conservation"]["accounted"] == 2


    def test_conservation_details_demoted(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        demoted = "\n".join([HERO_BLOCK, OPENER, QUICK_START, ARCHITECTURE,
                             API_REFERENCE, CONFIGURATION,
                             "<details>\n<summary>Roadmap</summary>\n\n"
                             "- teleportation\n\n</details>\n"])
        code, doc, _ = run_scorer(fixture, demoted, tmp_path)
        assert code == 0
        rows = sections_by_heading(doc)
        assert rows["## Roadmap"]["disposition"] == "details"
        assert rows["## Roadmap"]["ok"] is True


    def test_conservation_cut_declared(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        trimmed = "\n".join([HERO_BLOCK, OPENER, QUICK_START, ARCHITECTURE,
                             API_REFERENCE, CONFIGURATION])  # Roadmap cut
        cuts = [{"heading": "## Roadmap",
                 "reason": "speculative; lives in the issue tracker"}]
        code, doc, _ = run_scorer(fixture, trimmed, tmp_path, cuts=cuts)
        assert code == 0
        assert doc["gates"]["conservation"] is True
        assert sections_by_heading(doc)["## Roadmap"]["disposition"] == "cut"


    def test_conservation_only_home_cut_still_fails(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        trimmed = "\n".join([HERO_BLOCK, OPENER, QUICK_START, ARCHITECTURE,
                             CONFIGURATION, ROADMAP])  # API Reference cut
        cuts = [{"heading": "## API Reference", "reason": "too long for a README"}]
        code, doc, _ = run_scorer(fixture, trimmed, tmp_path, cuts=cuts)
        assert code == 2
        assert doc["gates"]["conservation"] is False
        row = sections_by_heading(doc)["## API Reference"]
        assert row["disposition"] == "cut-declared-but-only-home"
        assert row["ok"] is False


    def test_conservation_only_home_rehomed_to_linked_file_passes(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        (fixture / "docs").mkdir()
        (fixture / "docs" / "api.md").write_text(API_REFERENCE)
        rehomed = "\n".join([HERO_BLOCK, OPENER,
                             "See the [API reference](docs/api.md) for the full surface.\n",
                             QUICK_START, ARCHITECTURE, CONFIGURATION, ROADMAP])
        code, doc, _ = run_scorer(fixture, rehomed, tmp_path)
        assert code == 0
        row = sections_by_heading(doc)["## API Reference"]
        assert row["disposition"] == "linked"
        assert row["home"] == "docs/api.md"


# --- conservation: link-target guards (self/json/outside are never homes) ----

    def test_conservation_self_link_is_not_a_home(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        stripped = "\n".join([HERO_BLOCK, OPENER, QUICK_START,
                              "Everything else lives in the [original](README.md).\n"])
        code, doc, _ = run_scorer(fixture, stripped, tmp_path)
        assert code == 2
        assert doc["gates"]["conservation"] is False
        rows = sections_by_heading(doc)
        assert all(rows[h]["disposition"] == "missing" for h in rows)


    def test_conservation_json_link_is_not_a_home(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        stripped = "\n".join([HERO_BLOCK, OPENER, QUICK_START,
                              "Full details in the [manifest](ground-truth.json).\n"])
        code, doc, _ = run_scorer(fixture, stripped, tmp_path)
        assert code == 2
        assert doc["gates"]["conservation"] is False
        rows = sections_by_heading(doc)
        assert all(rows[h]["disposition"] == "missing" for h in rows)


    def test_conservation_link_outside_fixture_is_not_a_home(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        (tmp_path / "outside.md").write_text(SOURCE)  # full copy, but out of bounds
        stripped = "\n".join([HERO_BLOCK, OPENER, QUICK_START,
                              "Everything else lives [next door](../outside.md).\n"])
        code, doc, _ = run_scorer(fixture, stripped, tmp_path)
        assert code == 2
        assert doc["gates"]["conservation"] is False
        rows = sections_by_heading(doc)
        assert all(rows[h]["disposition"] == "missing" for h in rows)


# --- conservation: body-overlap (demote means move, VERBATIM) -----------------





    def test_conservation_hollow_details_rejected(self):
        tmp_path = self.tmp_path
        fixture = dense_fixture(tmp_path)
        hollow = GOOD + ("\n<details>\n<summary>Deep dive</summary>\n\n"
                         "### Deep dive\n\n</details>\n")
        code, doc, _ = run_scorer(fixture, hollow, tmp_path)
        assert code == 2
        assert doc["gates"]["conservation"] is False
        row = sections_by_heading(doc)["## Deep dive"]
        assert row["disposition"] == "body-gutted"
        assert row["ok"] is False
        assert "70%" in row["detail"]


    def test_conservation_verbatim_details_demotion_passes(self):
        tmp_path = self.tmp_path
        fixture = dense_fixture(tmp_path)
        demoted = GOOD + ("\n<details>\n<summary>Deep dive</summary>\n\n"
                          "### Deep dive\n\n" + DENSE_BODY + "\n</details>\n")
        code, doc, _ = run_scorer(fixture, demoted, tmp_path)
        assert code == 0
        row = sections_by_heading(doc)["## Deep dive"]
        assert row["disposition"] == "details"
        assert row["ok"] is True


    def test_conservation_kept_heading_with_gutted_body_rejected(self):
        tmp_path = self.tmp_path
        fixture = dense_fixture(tmp_path)
        hollow = GOOD + "\n## Deep dive\n\nSee elsewhere for the details.\n"
        code, doc, _ = run_scorer(fixture, hollow, tmp_path)
        assert code == 2
        row = sections_by_heading(doc)["## Deep dive"]
        assert row["disposition"] == "body-gutted"
        assert row["ok"] is False


    def test_conservation_kept_heading_with_verbatim_body_passes(self):
        tmp_path = self.tmp_path
        fixture = dense_fixture(tmp_path)
        kept = GOOD + "\n## Deep dive\n\n" + DENSE_BODY
        code, doc, _ = run_scorer(fixture, kept, tmp_path)
        assert code == 0
        row = sections_by_heading(doc)["## Deep dive"]
        assert row["disposition"] == "present"


    def test_conservation_hollow_linked_file_rejected(self):
        tmp_path = self.tmp_path
        fixture = dense_fixture(tmp_path)
        (fixture / "docs").mkdir()
        (fixture / "docs" / "deep.md").write_text("## Deep dive\n")  # heading only
        linked = GOOD + "\nDeep dives moved to [the appendix](docs/deep.md).\n"
        code, doc, _ = run_scorer(fixture, linked, tmp_path)
        assert code == 2
        row = sections_by_heading(doc)["## Deep dive"]
        assert row["disposition"] == "body-gutted"


# --- conservation: cut-reason quality (hard goal 2) ---------------------------

    def test_cut_reason_trimmed_for_length_rejected(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        trimmed = "\n".join([HERO_BLOCK, OPENER, QUICK_START, ARCHITECTURE,
                             API_REFERENCE, CONFIGURATION])  # Roadmap cut
        cuts = [{"heading": "## Roadmap", "reason": "Trimmed for length"}]
        code, doc, _ = run_scorer(fixture, trimmed, tmp_path, cuts=cuts)
        assert code == 2
        assert doc["gates"]["conservation"] is False
        row = sections_by_heading(doc)["## Roadmap"]
        assert row["disposition"] == "cut-reason-rejected"
        assert "trimmed for length" in row["detail"].lower()  # names the reason


    def test_cut_reason_synonyms_and_short_reasons_rejected(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        trimmed = "\n".join([HERO_BLOCK, OPENER, QUICK_START, ARCHITECTURE,
                             API_REFERENCE, CONFIGURATION])  # Roadmap cut
        for reason in ("trimmed", "for length", "brevity", "shortened", "old bits"):
            cuts = [{"heading": "## Roadmap", "reason": reason}]
            code, doc, _ = run_scorer(fixture, trimmed, tmp_path, cuts=cuts)
            assert code == 2, reason
            row = sections_by_heading(doc)["## Roadmap"]
            assert row["disposition"] == "cut-reason-rejected", reason
            assert reason in row["detail"], reason


# --- truth -------------------------------------------------------------------

    def test_truth_clean(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        code, doc, _ = run_scorer(fixture, GOOD, tmp_path)
        assert code == 0
        assert doc["gates"]["truth"] is True
        assert doc["truth"]["fired"] == 0
        assert doc["truth"]["fabrications"] == []


    def test_truth_fabricated(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        fabricated = GOOD.replace(
            "## Architecture",
            "- Fully supported: encrypted transport over TLS\n\n## Architecture")
        code, doc, _ = run_scorer(fixture, fabricated, tmp_path)
        assert code == 2
        assert doc["gates"]["truth"] is False
        assert doc["gates"]["conservation"] is True  # nothing was gutted
        fired = {f["id"] for f in doc["truth"]["fabrications"]}
        assert fired == {"c2"}
        assert doc["truth"]["fabrications"][0]["method"] in ("exact", "keywords")




    def test_truth_natural_phrasing_fires_via_keyword_stems(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path, gt=keyworded_ground_truth())
        natural = GOOD.replace(
            OPENER,
            OPENER + "\nAll queue traffic is encrypted end to end with TLS 1.3.\n")
        code, doc, _ = run_scorer(fixture, natural, tmp_path)
        assert code == 2
        assert doc["gates"]["truth"] is False
        fired = {f["id"] for f in doc["truth"]["fabrications"]}
        assert fired == {"c2"}
        fab = doc["truth"]["fabrications"][0]
        assert fab["method"] == "keywords"
        assert "encrypted" in fab["evidence_line"]


    def test_conservation_golden_subtree_is_not_a_home(self):
        tmp_path = self.tmp_path
        # The fixture's golden outputs are answer keys; linking one conserves nothing.
        fixture = make_fixture(tmp_path)
        golden = fixture / "golden"
        golden.mkdir()
        (golden / "good.md").write_text(SOURCE)  # contains every section verbatim
        gutted = "<h1 align=\"center\">shipit</h1>\n\nSee the [docs](golden/good.md).\n"
        code, doc, _ = run_scorer(fixture, gutted, tmp_path)
        assert code == 2
        assert doc["gates"]["conservation"] is False


    def test_truth_html_wrapped_prose_is_scanned(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path, gt=keyworded_ground_truth())
        wrapped = GOOD.replace(
            OPENER,
            OPENER + "\n<p>All queue traffic is encrypted end to end with TLS 1.3.</p>\n")
        code, doc, _ = run_scorer(fixture, wrapped, tmp_path)
        assert code == 2
        assert doc["gates"]["truth"] is False
        assert {f["id"] for f in doc["truth"]["fabrications"]} == {"c2"}


    def test_truth_heading_claim_is_scanned(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path, gt=keyworded_ground_truth())
        heading = GOOD.replace(
            "## Architecture",
            "## Encrypted TLS transport\n\n## Architecture")
        code, doc, _ = run_scorer(fixture, heading, tmp_path)
        assert code == 2
        assert doc["gates"]["truth"] is False
        assert {f["id"] for f in doc["truth"]["fabrications"]} == {"c2"}


    def test_truth_keyword_stems_in_source_text_do_not_fire(self):
        tmp_path = self.tmp_path
        # A sentence carrying the stems that also appears verbatim in the source
        # readme is not a new claim — the not-in-source condition must hold it back.
        disclaimer = "There is no TLS; nothing is encrypted at rest or in flight.\n"
        gt = keyworded_ground_truth()
        gt["sections"] = gt["sections"] + [{"heading": "## Security",
                                            "only_home": False}]
        source = SOURCE + "\n## Security\n\n" + disclaimer
        fixture = make_fixture(tmp_path, gt=gt, source=source)
        result = GOOD + "\n## Security\n\n" + disclaimer
        code, doc, _ = run_scorer(fixture, result, tmp_path)
        assert code == 0
        assert doc["gates"]["truth"] is True
        assert doc["truth"]["fired"] == 0


# --- face ---------------------------------------------------------------------

    def test_face_all_five(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        code, doc, _ = run_scorer(fixture, GOOD, tmp_path)
        assert code == 0
        assert doc["gates"]["face"] is True
        assert doc["face"]["score"] == 5
        assert all(c["ok"] for c in doc["face"]["checks"].values())


    def test_face_three_of_five_fails(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        # hero + tagline + badges, but no prose before the architecture heading
        # (opener fails) and no image/diagram/details (visual fails).
        bare = "\n".join([HERO_BLOCK, ARCHITECTURE, API_REFERENCE,
                          CONFIGURATION, ROADMAP])
        code, doc, _ = run_scorer(fixture, bare, tmp_path)
        assert code == 2
        assert doc["gates"]["face"] is False
        assert doc["face"]["score"] == 3
        checks = doc["face"]["checks"]
        assert checks["hero"]["ok"] and checks["tagline"]["ok"] and checks["badges"]["ok"]
        assert not checks["opener"]["ok"] and not checks["visual"]["ok"]
        # the other gates hold: face alone failed the run
        assert doc["gates"]["conservation"] is True
        assert doc["gates"]["truth"] is True


    def test_face_no_hero_fails_despite_four_of_five(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        no_hero = "\n".join([
            "# shipit\n",
            "*Ships anything, apologizes to no one.*\n",
            "[![build](https://img.shields.io/badge/build-passing-green)](https://ci.example.com)\n",
            OPENER, QUICK_START, ARCHITECTURE, API_REFERENCE, CONFIGURATION, ROADMAP])
        code, doc, _ = run_scorer(fixture, no_hero, tmp_path)
        assert code == 2
        assert doc["gates"]["face"] is False
        assert doc["face"]["checks"]["hero"]["ok"] is False
        assert doc["face"]["score"] == 4  # four of five pass, but hero is mandatory


    def test_face_no_tagline_fails_despite_four_of_five(self):
        tmp_path = self.tmp_path
        # hard-goals/flourish.md goal 4: hero AND tagline are both required.
        fixture = make_fixture(tmp_path)
        no_tagline = "\n".join([
            '<h1 align="center">shipit</h1>\n',
            "[![build](https://img.shields.io/badge/build-passing-green)](https://ci.example.com)\n",
            OPENER, QUICK_START, ARCHITECTURE, API_REFERENCE, CONFIGURATION, ROADMAP])
        code, doc, _ = run_scorer(fixture, no_tagline, tmp_path)
        assert code == 2
        assert doc["gates"]["face"] is False
        assert doc["face"]["checks"]["tagline"]["ok"] is False
        assert doc["face"]["checks"]["hero"]["ok"] is True


# --- voice (advisory only) -----------------------------------------------------

    def test_voice_kept(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        code, doc, _ = run_scorer(fixture, GOOD, tmp_path)
        assert code == 0
        assert doc["voice"]["advisory"] is True
        assert doc["voice"]["hook"]["status"] == "kept"


    def test_voice_flattened_is_advisory_only(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        flattened = GOOD.replace(
            "<em>Ships anything, apologizes to no one.</em>",
            "<em>Ships anything and apologizes to everyone.</em>")
        code, doc, _ = run_scorer(fixture, flattened, tmp_path)
        assert code == 0  # every gate passes; a flattened hook never gates
        assert doc["verdict"] == "PASS"
        assert doc["voice"]["hook"]["status"] == "flattened"
        assert doc["voice"]["hook"]["overlap"] >= 0.6


    def test_voice_absent_is_advisory_only(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        hookless = GOOD.replace(
            "<em>Ships anything, apologizes to no one.</em>",
            "<em>A deployment pipeline utility.</em>")
        code, doc, _ = run_scorer(fixture, hookless, tmp_path)
        assert code == 0
        assert doc["voice"]["hook"]["status"] == "absent"


# --- plumbing --------------------------------------------------------------------

    def test_human_output_and_missing_ground_truth(self):
        tmp_path = self.tmp_path
        fixture = make_fixture(tmp_path)
        code, _, proc = run_scorer(fixture, GOOD, tmp_path, as_json=False)
        assert code == 0
        assert "verdict: PASS" in proc.stdout
        assert "voice (advisory, never gates)" in proc.stdout

        empty = tmp_path / "empty"
        empty.mkdir()
        result = tmp_path / "RESULT.md"
        proc = subprocess.run(
            [sys.executable, str(SCORER), "--fixture", str(empty),
             "--result", str(result), "--json"],
            capture_output=True, text=True)
        assert proc.returncode == 1  # operational error, not a gate failure
        assert "ground-truth.json" in proc.stderr


# --- the report gates: hard goals 5, 6 and 9 ----------------------------------

REPORT = """# flourish report — shipit

claims: 6 — certified: 4, cut: 1, markered: 1

## Conservation ledger

| section | disposition | now lives |
| --- | --- | --- |
| Roadmap | cut | the issue tracker |

## The rewrite

```diff
--- README.md
+++ README.md
-# shipit
+<h1 align="center">shipit</h1>
```

## Verification

```sh
bash tests/all.sh
```

GREEN - all four CI suites pass locally.
"""

FILLER = ("The queue drains steadily and the workers keep their own pace. " * 60)


class FlourishReportGateTests(unittest.TestCase):
    """Hard goals 5, 6 and 9 — graded off the run's report, not the rewrite."""

    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_path, True)

    def test_report_gates_pass_on_a_complete_report(self):
        fixture = make_fixture(self.tmp_path)
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path, report=REPORT)
        assert code == 0
        assert doc["report"]["supplied"] is True
        assert doc["gates"]["claim_counts"] is True
        assert doc["gates"]["shown"] is True
        assert doc["gates"]["verification"] is True

    def test_report_gates_are_not_enforced_without_a_report(self):
        fixture = make_fixture(self.tmp_path)
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path)
        assert code == 0
        assert doc["report"]["supplied"] is False
        assert "claim_counts" not in doc["gates"]

    def test_goal5_counts_must_sum_to_the_claim_set(self):
        fixture = make_fixture(self.tmp_path)
        bad = REPORT.replace("certified: 4", "certified: 3")
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path, report=bad)
        assert code == 2
        assert doc["gates"]["claim_counts"] is False
        assert doc["claim_counts"]["sum"] == 5
        assert doc["claim_counts"]["claim_total"] == 6

    def test_goal5_missing_a_count_fails(self):
        fixture = make_fixture(self.tmp_path)
        bad = REPORT.replace("claims: 6 — certified: 4, cut: 1, markered: 1",
                             "claims: 6 — certified: 4, cut: 1")
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path, report=bad)
        assert code == 2
        assert doc["gates"]["claim_counts"] is False
        assert doc["claim_counts"]["counts"]["markered"] is None

    def test_goal6_needs_the_diff_and_the_ledger(self):
        fixture = make_fixture(self.tmp_path)
        no_diff = REPORT.replace("```diff", "```text")
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path, report=no_diff)
        assert code == 2
        assert doc["gates"]["shown"] is False
        assert doc["shown"]["diff_fence"] is False

        no_ledger = REPORT.replace("Conservation ledger", "Notes")
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path, report=no_ledger)
        assert code == 2
        assert doc["gates"]["shown"] is False
        assert doc["shown"]["ledger"] is False

    def test_goal6_accepts_the_full_result_inlined(self):
        fixture = make_fixture(self.tmp_path)
        inlined = REPORT.replace("```diff", "```text") + "\n" + GOOD
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path, report=inlined)
        assert code == 0
        assert doc["shown"]["full_result_inlined"] is True

    def test_goal9_needs_a_command_and_a_verdict(self):
        fixture = make_fixture(self.tmp_path)
        no_verdict = REPORT.replace("GREEN - all four CI suites pass locally.", "")
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path, report=no_verdict)
        assert code == 2
        assert doc["gates"]["verification"] is False
        assert doc["verification"]["verdict_line"] is None

        no_command = REPORT.replace("bash tests/all.sh", "the usual suite")
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path, report=no_command)
        assert code == 2
        assert doc["gates"]["verification"] is False

    def test_goal9_accepts_the_declared_escape(self):
        fixture = make_fixture(self.tmp_path)
        escaped = REPORT.replace(
            "```sh\nbash tests/all.sh\n```\n\nGREEN - all four CI suites pass locally.",
            "This repository declares no verification entry point.")
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path, report=escaped)
        assert code == 0
        assert doc["verification"]["declared_no_entry_point"] is True


# --- counted on the result: hard goals 7, 8 and 10 ----------------------------

class FlourishResultCountTests(unittest.TestCase):
    """Hard goals 7, 8 and 10 — greps and counts over the written result."""

    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_path, True)

    def test_goal7_quick_start_within_400_words(self):
        fixture = make_fixture(self.tmp_path)
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path)
        assert code == 0
        assert doc["gates"]["quick_start"] is True
        assert doc["quick_start"]["words_to_quick_start"] < 400

    def test_goal7_essay_before_the_quick_start_fails(self):
        fixture = make_fixture(self.tmp_path)
        buried = GOOD.replace(QUICK_START, FILLER + "\n\n" + QUICK_START)
        code, doc, _ = run_scorer(fixture, buried, self.tmp_path)
        assert code == 2
        assert doc["gates"]["quick_start"] is False
        assert doc["quick_start"]["words_to_quick_start"] > 400

    def test_goal7_pure_explainer_is_exempt(self):
        fixture = make_fixture(self.tmp_path)
        explainer = GOOD.replace("## Quick start", "## How it thinks")
        code, doc, _ = run_scorer(fixture, explainer, self.tmp_path)
        assert code == 0
        assert doc["quick_start"]["heading_found"] is False
        assert doc["gates"]["quick_start"] is True

    def test_goal8_open_page_over_the_band_fails(self):
        fixture = make_fixture(self.tmp_path)
        bloated = GOOD + "\n" + (FILLER * 5)
        code, doc, _ = run_scorer(fixture, bloated, self.tmp_path)
        assert code == 2
        assert doc["gates"]["length"] is False
        assert doc["length"]["open_page_words"] > 2500

    def test_goal8_details_content_is_rent_free(self):
        fixture = make_fixture(self.tmp_path)
        collapsed = GOOD + "\n<details>\n<summary>Appendix</summary>\n\n" \
            + (FILLER * 5) + "\n</details>\n"
        code, doc, _ = run_scorer(fixture, collapsed, self.tmp_path)
        assert code == 0
        assert doc["gates"]["length"] is True
        assert doc["length"]["open_page_words"] <= 2500

    def test_goal10_vertical_mermaid_passes(self):
        fixture = make_fixture(self.tmp_path)
        code, doc, _ = run_scorer(fixture, GOOD, self.tmp_path)
        assert code == 0
        assert doc["gates"]["diagram"] is True
        assert doc["diagram"]["mermaid_fences"] == 1
        assert doc["diagram"]["vertical_declarations"] == 1

    def test_goal10_no_diagram_fails(self):
        fixture = make_fixture(self.tmp_path)
        plain = GOOD.replace("```mermaid\nflowchart TD\n", "```\n")
        code, doc, _ = run_scorer(fixture, plain, self.tmp_path)
        assert code == 2
        assert doc["gates"]["diagram"] is False
        assert doc["diagram"]["mermaid_fences"] == 0

    def test_goal10_horizontal_mermaid_fails(self):
        fixture = make_fixture(self.tmp_path)
        sideways = GOOD.replace("flowchart TD", "flowchart LR")
        code, doc, _ = run_scorer(fixture, sideways, self.tmp_path)
        assert code == 2
        assert doc["gates"]["diagram"] is False
        assert doc["diagram"]["horizontal_declarations"] == 1

    def test_goal10_direction_lr_subgraph_fails(self):
        fixture = make_fixture(self.tmp_path)
        sideways = GOOD.replace("flowchart TD\n", "flowchart TD\n    direction LR\n")
        code, doc, _ = run_scorer(fixture, sideways, self.tmp_path)
        assert code == 2
        assert doc["gates"]["diagram"] is False

    def test_goal10_box_drawing_art_fails(self):
        fixture = make_fixture(self.tmp_path)
        boxed = GOOD + "\n```\n\u250c\u2500\u2510\n\u2502 q \u2502\n\u2514\u2500\u2518\n```\n"
        code, doc, _ = run_scorer(fixture, boxed, self.tmp_path)
        assert code == 2
        assert doc["gates"]["diagram"] is False
        assert doc["diagram"]["box_drawing_lines"] == 3
