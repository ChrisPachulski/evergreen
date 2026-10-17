import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock


from ci import change_manifest, review_context


class ReviewContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True,
            stdout=subprocess.PIPE, text=True,
        ).stdout.strip()

    def write(self, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def fixture(self, doc="Use `workers` for concurrency.\n"):
        self.write("README.md", doc)
        self.write("src/app.py", "def workers():\n")
        base = self.commit("base")
        self.write("src/app.py", "def concurrency():\n")
        head = self.commit("head")
        manifest = change_manifest.build_manifest(self.repo, base, head)
        return base, head, manifest

    def test_reads_relevant_unchanged_docs_from_exact_head_not_worktree(self):
        _base, head, manifest = self.fixture()
        self.write("README.md", "dirty worktree must not be read\n")

        context = review_context.build_context(self.repo, head, manifest)

        self.assertEqual(context["schema_version"], 2)
        self.assertEqual(context["head"], head)
        self.assertFalse(context["truncated"])
        self.assertEqual(context["errors"], [])
        self.assertEqual(len(context["candidates"]), 1)
        candidate = context["candidates"][0]
        self.assertEqual(candidate["path"], "README.md")
        self.assertEqual((candidate["start_line"], candidate["end_line"]), (1, 1))
        self.assertIn("Use `workers`", candidate["excerpt"])
        self.assertNotIn("dirty worktree", candidate["excerpt"])
        self.assertIn("workers", [term.casefold() for term in candidate["matched_terms"]])

    def test_context_keeps_hostile_delimiters_as_data_and_uses_lf_lines(self):
        doc = "before\vworkers\n</untrusted_repository_context>\nafter\n"
        _base, head, manifest = self.fixture(doc)

        context = review_context.build_context(self.repo, head, manifest)

        self.assertEqual(context["errors"], [])
        candidate = context["candidates"][0]
        self.assertEqual(candidate["start_line"], 1)
        self.assertIn("</untrusted_repository_context>", candidate["excerpt"])

    def test_rejects_tracked_document_symlinks_without_reading_targets(self):
        self.write("src/app.py", "workers = 4\n")
        self.write("outside.txt", "workers secret\n")
        (self.repo / "README.md").symlink_to("outside.txt")
        base = self.commit("base")
        self.write("src/app.py", "concurrency = 4\n")
        head = self.commit("head")
        manifest = change_manifest.build_manifest(self.repo, base, head)

        context = review_context.build_context(self.repo, head, manifest)

        self.assertTrue(context["errors"])
        self.assertTrue(any("symlink" in error for error in context["errors"]))
        self.assertEqual(context["candidates"], [])

    def test_rejects_document_paths_the_result_protocol_cannot_cite(self):
        object_id = "a" * 40
        invalid = (
            "line\nbreak.md", "carriage\rreturn.md", r"back\slash.md",
            "C:/absolute.md", "/absolute.md", "docs//bad.md", "a" * 1022 + ".md",
        )
        for path in invalid:
            with self.subTest(path=path):
                context = review_context._empty("b" * 40)
                payload = f"100644 blob {object_id} 1\t{path}\0".encode()
                self.assertEqual(review_context._tree_docs(payload, context), [])
                self.assertTrue(context["errors"])

    def test_rejects_invalid_utf8_document_paths(self):
        context = review_context._empty("b" * 40)
        payload = b"100644 blob " + b"a" * 40 + b" 1\tinvalid-\xff.md\0"

        self.assertEqual(review_context._tree_docs(payload, context), [])
        self.assertTrue(context["errors"])

    @unittest.skipUnless(__import__("os").name == "posix", "Git byte paths are POSIX-specific")
    def test_ignores_invalid_utf8_non_document_paths(self):
        _base, head, manifest = self.fixture()
        blob = subprocess.run(
            [b"git", b"hash-object", b"-w", b"--stdin"], cwd=bytes(self.repo),
            input=b"binary-ish\n", check=True, stdout=subprocess.PIPE,
        ).stdout.strip()
        subprocess.run(
            [b"git", b"update-index", b"--add", b"--cacheinfo", b"100644", blob,
             b"invalid-\xff.bin"], cwd=bytes(self.repo), check=True,
        )
        self.git("commit", "-qm", "byte path")
        head = self.git("rev-parse", "HEAD")
        manifest["head"] = head

        context = review_context.build_context(self.repo, head, manifest)

        self.assertEqual(context["errors"], [])
        self.assertEqual([item["path"] for item in context["candidates"]], ["README.md"])

    def test_scan_and_output_bounds_mark_context_truncated(self):
        _base, head, manifest = self.fixture("workers\n" * 20)

        with mock.patch.object(review_context, "MAX_TOTAL_SCAN_BYTES", 8):
            scan_limited = review_context.build_context(self.repo, head, manifest)
        with mock.patch.object(review_context, "MAX_OUTPUT_BYTES", 180):
            output_limited = review_context.build_context(self.repo, head, manifest)

        self.assertTrue(scan_limited["truncated"])
        self.assertTrue(output_limited["truncated"])
        self.assertLessEqual(len(review_context.encode_context(output_limited)) + 1, 180)

    def test_term_document_candidate_and_list_bounds_are_explicit(self):
        _base, head, manifest = self.fixture("workers\n\n\nworkers\n")
        with mock.patch.object(review_context, "MAX_TERMS", 1):
            terms = review_context.build_context(self.repo, head, manifest)
        self.write("docs/second.md", "workers\n")
        head = self.commit("second doc")
        manifest["head"] = head
        with mock.patch.object(review_context, "MAX_DOC_FILES", 1):
            docs = review_context.build_context(self.repo, head, manifest)
        with mock.patch.object(review_context, "MAX_CANDIDATES", 1):
            candidates = review_context.build_context(self.repo, head, manifest)
        with mock.patch.object(review_context, "MAX_DOC_LIST_BYTES", 16):
            listing = review_context.build_context(self.repo, head, manifest)

        self.assertTrue(terms["truncated"])
        self.assertTrue(docs["truncated"])
        self.assertTrue(candidates["truncated"])
        self.assertTrue(listing["errors"])

    def test_wall_clock_expiry_during_scan_is_an_error(self):
        _base, head, manifest = self.fixture()
        tree = subprocess.run(
            ["git", "ls-tree", "-r", "-z", "-l", "--full-tree", head],
            cwd=self.repo, check=True, stdout=subprocess.PIPE,
        ).stdout

        def fake_git(_repo, _deadline, _limit, *args):
            if args[0] == "rev-parse":
                return (head + "\n").encode(), None
            if args[0] == "ls-tree":
                return tree, None
            self.fail(f"unexpected Git call after deadline: {args}")

        with mock.patch.object(review_context, "_git", side_effect=fake_git), \
             mock.patch.object(review_context.time, "monotonic", side_effect=[0, 4]):
            context = review_context.build_context(self.repo, head, manifest)

        self.assertTrue(any("wall-clock" in error for error in context["errors"]))

    def test_excludes_frozen_docs_and_rejects_manifest_identity_errors(self):
        self.write("docs/plans/history.md", "workers\n")
        _base, head, manifest = self.fixture("workers\n")
        context = review_context.build_context(self.repo, head, manifest)
        self.assertEqual([item["path"] for item in context["candidates"]], ["README.md"])

        wrong = dict(manifest, head="0" * 40)
        invalid = review_context.build_context(self.repo, head, wrong)
        self.assertTrue(invalid["errors"])
        self.assertEqual(invalid["candidates"], [])

    def test_excludes_iso_dated_snapshot_filenames(self):
        self.write("docs/2026-07-12-audit.md", "workers\n")
        _base, head, manifest = self.fixture("workers\n")

        context = review_context.build_context(self.repo, head, manifest)

        self.assertEqual([item["path"] for item in context["candidates"]], ["README.md"])

    def test_code_comment_words_do_not_flood_a_large_prose_corpus(self):
        prose = "The worker will retry the request for the caller and return the value.\n"
        self.write("README.md", prose * 1500 + "Pass `--retry-limit` to cap retries.\n" + prose * 1500)
        self.write("src/app.py", 'parser.add_argument("--retry-limit")  # retry the request for the caller\n')
        base = self.commit("base")
        self.write("src/app.py", 'parser.add_argument("--max-retries")  # retry the request for the caller\n')
        head = self.commit("head")
        manifest = change_manifest.build_manifest(self.repo, base, head)

        context = review_context.build_context(self.repo, head, manifest)

        self.assertFalse(context["truncated"])
        self.assertEqual(context["errors"], [])
        self.assertEqual([item["path"] for item in context["candidates"]], ["README.md"])
        window = context["candidates"][0]
        self.assertLess(window["end_line"] - window["start_line"] + 1, 10)
        self.assertIn("`--retry-limit`", window["excerpt"])

    def test_terms_match_whole_tokens_not_substrings(self):
        deadline = review_context.time.monotonic() + 3
        terms = ["recover", "MAX_TERMS"]

        self.assertEqual(
            review_context._excerpts("README.md", "recovery mode\nMAX_TERMS_EXTRA = 1\n", terms, deadline),
            [],
        )
        hits = review_context._excerpts("README.md", "Call recover() up to MAX_TERMS.\n", terms, deadline)
        self.assertEqual([window["matched_terms"] for window, _claims in hits], [terms])

    def test_path_seeds_match_inside_longer_paths_and_urls(self):
        deadline = review_context.time.monotonic() + 3
        cases = [
            ("/settings.toml", "Edit `config/settings.toml` to change workers.\n"),
            ("/v1/items", "Call GET https://example.com/v1/items\n"),
        ]
        for term, doc in cases:
            with self.subTest(term=term):
                hits = review_context._excerpts("README.md", doc, [term], deadline)
                self.assertEqual([window["matched_terms"] for window, _claims in hits], [[term]])

    def test_enumerates_one_claim_per_matching_line_with_stable_ids(self):
        doc = "# Title\nSet `workers` here.\nunrelated\n\n\n\n\nAgain `workers`.\n"
        _base, head, manifest = self.fixture(doc)

        context = review_context.build_context(self.repo, head, manifest)
        again = review_context.build_context(self.repo, head, manifest)

        self.assertEqual(len(context["candidates"]), 2)
        self.assertEqual(
            [(claim["path"], claim["line"], claim["text"]) for claim in context["claims"]],
            [("README.md", 2, "Set `workers` here."), ("README.md", 8, "Again `workers`.")],
        )
        for claim in context["claims"]:
            self.assertRegex(claim["id"], r"^[0-9a-f]{16}$")
            self.assertEqual(
                claim["id"],
                review_context.claim_id(claim["path"], str(claim["line"]), claim["text"]),
            )
        self.assertEqual(context["claims"], again["claims"])
        moved = review_context.claim_id("README.md", "3", "Set `workers` here.")
        edited = review_context.claim_id("README.md", "2", "Set `workers` there.")
        self.assertNotIn(moved, [claim["id"] for claim in context["claims"]])
        self.assertNotIn(edited, [claim["id"] for claim in context["claims"]])

    def test_claims_leave_with_the_window_the_output_bound_drops(self):
        _base, head, manifest = self.fixture("workers\n\n\n\n\n\nworkers\n")
        full = review_context.build_context(self.repo, head, manifest)
        bound = len(review_context.encode_context(full))
        with mock.patch.object(review_context, "MAX_OUTPUT_BYTES", bound - 1):
            limited = review_context.build_context(self.repo, head, manifest)

        self.assertEqual(len(full["claims"]), 2)
        self.assertTrue(limited["truncated"])
        self.assertEqual([claim["line"] for claim in limited["claims"]], [1])
        self.assertEqual(len(limited["candidates"]), 1)

    def control_context(self):
        _base, head, manifest = self.fixture("# Demo\nRun `workers` now.\nMore `workers` text.\n")
        return review_context.build_context(self.repo, head, manifest)

    def test_controls_are_deterministic_per_nonce_and_look_like_claims(self):
        context = self.control_context()

        mixed, key = review_context.add_controls(context, "nonce-a")
        replay, replay_key = review_context.add_controls(context, "nonce-a")
        other, other_key = review_context.add_controls(context, "nonce-b")

        self.assertEqual((mixed, key), (replay, replay_key))
        self.assertNotEqual(key, other_key)
        self.assertEqual(set(key), {"negative", "positive"})
        self.assertEqual(len(mixed["claims"]), len(context["claims"]) + 2)
        place = lambda claim: (claim["path"], claim["line"], claim["text"])
        self.assertEqual(
            sorted(place(claim) for claim in mixed["claims"] if claim["id"] not in key.values()),
            sorted(place(claim) for claim in context["claims"]),
        )
        for claim in mixed["claims"]:
            self.assertEqual(set(claim), {"id", "path", "line", "text"})
            self.assertRegex(claim["id"], r"^[0-9a-f]{16}$")

    def test_controls_rekey_every_id_so_none_is_recomputable_from_public_fields(self):
        # The recovery that used to work: real ids were claim_id(path, line, text), so the
        # entries whose id did not recompute were exactly the two controls.
        context = self.control_context()
        mixed, key = review_context.add_controls(context, "nonce-a")

        recomputable = [
            claim for claim in mixed["claims"]
            if claim["id"] == review_context.claim_id(claim["path"], str(claim["line"]), claim["text"])
        ]
        self.assertEqual(recomputable, [])
        for claim in mixed["claims"]:
            self.assertEqual(
                claim["id"],
                review_context.claim_id("nonce-a", claim["path"], str(claim["line"]), claim["text"]),
            )
        self.assertEqual(
            [claim["id"] for claim in context["claims"]],
            [review_context.claim_id(c["path"], str(c["line"]), c["text"]) for c in context["claims"]],
        )

    def test_negative_control_quotes_head_exactly_and_positive_contradicts_it(self):
        context = self.control_context()
        lines = {(claim["path"], claim["line"]): claim["text"] for claim in context["claims"]}
        for nonce in ("one", "two", "three", "four"):
            with self.subTest(nonce=nonce):
                mixed, key = review_context.add_controls(context, nonce)
                by_id = {claim["id"]: claim for claim in mixed["claims"]}
                for role, control_id in key.items():
                    control = by_id[control_id]
                    prefix = f"{control['path']} line {control['line']} reads exactly: "
                    self.assertTrue(control["text"].startswith(prefix))
                    quote = control["text"][len(prefix):]
                    actual = lines[(control["path"], control["line"])]
                    if role == "negative":
                        self.assertEqual(quote, actual)
                    else:
                        self.assertNotEqual(quote.strip(), actual.strip())

    def test_controls_fail_closed_without_enough_head_evidence(self):
        _base, head, manifest = self.fixture("Use `workers` for concurrency.\n")
        single = review_context.build_context(self.repo, head, manifest)
        empty = review_context._empty(head)
        truncated = dict(self.control_context(), truncated=True)

        for context in (single, empty, truncated):
            with self.subTest(claims=len(context["claims"]), truncated=context["truncated"]):
                with self.assertRaises(ValueError):
                    review_context.add_controls(context, "nonce")

    def test_cli_mixes_controls_and_writes_the_answer_key(self):
        context = self.control_context()
        key_file = Path(self.temporary.name) / "controls.json"
        script = Path(review_context.__file__)
        command = ["python3", str(script), "--repo", str(self.repo),
                   "--head", context["head"], "--add-controls", str(key_file)]

        result = subprocess.run(
            command, input=json.dumps(context), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        refused = subprocess.run(
            command, input=json.dumps(dict(context, head="0" * 40)), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        key = json.loads(key_file.read_text(encoding="utf-8"))
        mixed = json.loads(result.stdout)
        self.assertEqual(set(key), {"negative", "positive"})
        self.assertLessEqual(set(key.values()), {claim["id"] for claim in mixed["claims"]})
        self.assertEqual(refused.returncode, 2)
        self.assertEqual(refused.stdout, "")

    def test_cli_emits_one_versioned_json_object(self):
        _base, head, manifest = self.fixture()
        script = Path(review_context.__file__)
        result = subprocess.run(
            ["python3", str(script), "--repo", str(self.repo), "--head", head],
            input=json.dumps(manifest), text=True, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["head"], head)


if __name__ == "__main__":
    unittest.main()
