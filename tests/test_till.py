import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "evergreen"


class GapsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def track(self, files):
        for path, content in files.items():
            target = self.repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.repo,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_reports_symbol_kind_path_line_and_rank_for_each_public_declaration(self):
        from evergreen.till import till

        self.track({"api.py": (
            "class Client:\n"
            "    def fetch(self):\n"
            "        pass\n"
            "def helper():\n"
            "    pass\n"
        )})

        report = till(self.repo)

        self.assertEqual(report.warnings, ())
        self.assertEqual(
            [(c.symbol, c.kind, c.path, c.line, c.rank) for c in report.candidates],
            [
                ("Client", "class", "api.py", 1, 1),
                ("helper", "def", "api.py", 4, 2),
                ("fetch", "def", "api.py", 2, 3),
            ],
        )

    def test_ranks_are_one_based_consecutive_and_gapless(self):
        from evergreen.till import till

        self.track({
            "a.py": "class A:\n    def one(self):\n        pass\n",
            "b.py": "def two():\n    pass\n\ndef three():\n    pass\n",
        })

        report = till(self.repo)

        self.assertGreater(len(report.candidates), 0)
        self.assertEqual(
            [c.rank for c in report.candidates],
            list(range(1, len(report.candidates) + 1)),
        )

    def test_run_twice_yields_an_identical_report(self):
        from evergreen.till import till

        self.track({
            "a.py": "class A:\n    def one(self):\n        pass\n",
            "b.go": "func Exported() {}\n",
        })

        self.assertEqual(till(self.repo), till(self.repo))

    def test_cli_json_output_is_byte_identical_across_runs(self):
        self.track({"api.py": "class Client:\n    pass\ndef helper():\n    pass\n"})

        first = self.run_cli("till", "--json")
        second = self.run_cli("till", "--json")

        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)
        payload = json.loads(first.stdout)
        self.assertEqual(set(payload), {
            "schema_version", "candidates",
            "source_files_in_scope", "source_files_scanned", "warnings",
        })
        self.assertEqual(payload["schema_version"], 1)

    def test_module_scope_precedes_members_and_types_precede_callables(self):
        # Structural order is only the tie-break: nothing here is referenced outside its
        # file, so every candidate has zero usage signal and depth/tier decide.
        from evergreen.till import till

        self.track({"api.py": (
            "def alpha():\n"
            "    pass\n"
            "class Zeta:\n"
            "    def member(self):\n"
            "        pass\n"
        )})

        report = till(self.repo)

        self.assertEqual(
            [c.symbol for c in report.candidates],
            ["Zeta", "alpha", "member"],
        )

    def test_nested_and_private_declarations_are_excluded(self):
        from evergreen.till import till

        self.track({"api.py": (
            "def _hidden():\n"
            "    pass\n"
            "def outer():\n"
            "    def closure():\n"
            "        pass\n"
            "class _Private:\n"
            "    def member(self):\n"
            "        pass\n"
        )})

        report = till(self.repo)

        self.assertEqual([c.symbol for c in report.candidates], ["outer"])

    def test_go_uppercase_and_rust_pub_visibility_rules_apply(self):
        from evergreen.till import till

        self.track({
            "lib.go": "func hidden() {}\nfunc Shown() {}\n",
            "lib.rs": "fn hidden() {}\npub fn shown() {}\n",
        })

        report = till(self.repo)

        self.assertEqual(
            sorted((c.symbol, c.path) for c in report.candidates),
            [("Shown", "lib.go"), ("shown", "lib.rs")],
        )

    def test_untracked_source_files_are_outside_scope_but_surfaced(self):
        from evergreen.till import till

        self.track({"tracked.py": "def tracked():\n    pass\n"})
        (self.repo / "scratch.py").write_text("def scratch():\n    pass\n", encoding="utf-8")

        report = till(self.repo)

        self.assertEqual([c.symbol for c in report.candidates], ["tracked"])
        self.assertEqual(report.source_files_in_scope, 1)
        self.assertEqual(report.source_files_scanned, 1)
        # Deliberately excluded, but never silently: the report says so without failing closed.
        self.assertTrue(any("untracked" in item for item in report.warnings))
        self.assertFalse(any("truncated" in item for item in report.warnings))

    def test_unparsed_language_files_are_surfaced_by_name(self):
        from evergreen.till import till

        self.track({
            "api.py": "def real():\n    pass\n",
            "Main.java": "public class Main {}\n",
            "build.sh": "echo build\n",
            "LICENSE": "MIT\n",
        })

        report = till(self.repo)

        self.assertEqual([c.symbol for c in report.candidates], ["real"])
        self.assertEqual(report.source_files_in_scope, 1)
        # The exclusion names its files: a bare count once hid what was dropped.
        self.assertTrue(any(
            "2 tracked source file(s) outside inventory" in item
            and "Main.java" in item and "build.sh" in item
            for item in report.warnings
        ))
        self.assertFalse(any("truncated" in item for item in report.warnings))

    def test_python_shebang_scripts_join_the_inventory(self):
        from evergreen.till import till

        self.track({
            "api.py": "def real():\n    pass\n",
            "bin/tool": "#!/usr/bin/env python3\ndef cli():\n    pass\n",
            "hook": "#!/bin/sh\necho hi\n",
        })

        report = till(self.repo)

        # The CLI entry point is public surface; a shell script stays outside inventory.
        self.assertEqual(
            sorted((c.symbol, c.path) for c in report.candidates),
            [("cli", "bin/tool"), ("real", "api.py")],
        )
        self.assertEqual(report.source_files_in_scope, 2)
        self.assertEqual(report.source_files_scanned, 2)
        self.assertTrue(any(
            "1 tracked source file(s) outside inventory" in item and "hook" in item
            for item in report.warnings
        ))
        self.assertFalse(any("truncated" in item for item in report.warnings))

    def test_test_and_vendor_paths_are_outside_scope(self):
        from evergreen.till import till

        self.track({
            "api.py": "def real():\n    pass\n",
            "tests/test_x.py": "def test_real():\n    pass\n",
            "conftest.py": "def fixture():\n    pass\n",
            "vendor/lib.go": "func Vendored() {}\n",
            "a_test.go": "func TestReal() {}\n",
            "test.py": "def bare_test_helper():\n    pass\n",
        })

        report = till(self.repo)

        self.assertEqual([c.symbol for c in report.candidates], ["real"])
        self.assertEqual(report.source_files_in_scope, 1)
        self.assertEqual(report.source_files_scanned, 1)

    def test_scanned_count_equals_in_scope_count_when_nothing_truncates(self):
        from evergreen.till import till

        self.track({
            "a.py": "def one():\n    pass\n",
            "b.py": "def two():\n    pass\n",
        })

        report = till(self.repo)

        self.assertEqual(report.source_files_scanned, report.source_files_in_scope)
        self.assertEqual(report.source_files_in_scope, 2)
        self.assertEqual(report.warnings, ())

    def test_source_file_bound_emits_truncated_warning(self):
        from evergreen import till as module

        self.track({
            "a.py": "def one():\n    pass\n",
            "b.py": "def two():\n    pass\n",
            "c.py": "def three():\n    pass\n",
        })

        with mock.patch.object(module, "MAX_GAP_SOURCE_FILES", 1):
            report = module.till(self.repo)

        self.assertTrue(any("truncated" in item for item in report.warnings))
        self.assertLess(report.source_files_scanned, report.source_files_in_scope)
        self.assertEqual([c.symbol for c in report.candidates], ["one"])

    def test_scan_byte_budget_emits_truncated_warning(self):
        from evergreen import till as module

        self.track({
            "a.py": "def one():\n    pass\n",
            "b.py": "def two():\n    pass\n",
        })

        with mock.patch.object(module, "MAX_GAP_SCAN_BYTES", 1):
            report = module.till(self.repo)

        self.assertTrue(any("truncated" in item for item in report.warnings))
        self.assertLess(report.source_files_scanned, report.source_files_in_scope)
        self.assertEqual([c.symbol for c in report.candidates], ["one"])

    def test_unreadable_tracked_file_emits_truncated_warning(self):
        from evergreen.till import till

        self.track({
            "a.py": "def one():\n    pass\n",
            "b.py": "def two():\n    pass\n",
        })
        (self.repo / "a.py").unlink()

        report = till(self.repo)

        self.assertTrue(any("truncated" in item for item in report.warnings))
        self.assertLess(report.source_files_scanned, report.source_files_in_scope)
        self.assertEqual([c.symbol for c in report.candidates], ["two"])

    def test_candidate_cap_keeps_rank_prefix_and_warns(self):
        from evergreen import till as module

        self.track({"api.py": (
            "class Client:\n"
            "    def fetch(self):\n"
            "        pass\n"
            "def helper():\n"
            "    pass\n"
        )})

        with mock.patch.object(module, "MAX_GAP_CANDIDATES", 2):
            report = module.till(self.repo)

        self.assertTrue(any("truncated" in item for item in report.warnings))
        self.assertEqual(
            [(c.symbol, c.rank) for c in report.candidates],
            [("Client", 1), ("helper", 2)],
        )

    def test_kind_is_the_declaration_keyword_verbatim(self):
        from evergreen.till import till

        self.track({
            "shapes.go": "type Point struct {}\n",
            "colors.rs": "pub enum Color {}\n",
            "props.ts": "export interface Props {}\n",
        })

        report = till(self.repo)

        self.assertEqual(
            sorted((c.symbol, c.kind) for c in report.candidates),
            [("Color", "enum"), ("Point", "type"), ("Props", "interface")],
        )

    def test_scanned_shortfall_always_pairs_with_truncated_warning(self):
        from evergreen import till as module

        self.track({
            "a.py": "def one():\n    pass\n",
            "b.py": "def two():\n    pass\n",
            "c.py": "def three():\n    pass\n",
        })
        (self.repo / "c.py").unlink()

        for patched in (
            mock.patch.object(module, "MAX_GAP_SOURCE_FILES", 1),
            mock.patch.object(module, "MAX_GAP_SCAN_BYTES", 1),
            mock.patch.object(module, "GAP_SCAN_TIMEOUT_SECONDS", 0),
        ):
            with patched:
                report = module.till(self.repo)
            if report.source_files_scanned < report.source_files_in_scope:
                self.assertTrue(any("truncated" in item for item in report.warnings))

    def test_non_repository_directory_fails_closed_with_truncated_warning(self):
        from evergreen.till import till

        report = till(self.repo)

        self.assertEqual(report.candidates, ())
        self.assertEqual(report.source_files_in_scope, 0)
        self.assertEqual(report.source_files_scanned, 0)
        self.assertTrue(any("truncated" in item for item in report.warnings))

    def test_scope_paths_narrow_inventory_and_keep_ranks_gapless(self):
        from evergreen.till import till

        self.track({
            "src/a.py": "class A:\n    pass\ndef helper():\n    pass\n",
            "lib/b.py": "def other():\n    pass\n",
        })

        report = till(self.repo, ("src",))

        self.assertEqual([c.path for c in report.candidates], ["src/a.py", "src/a.py"])
        self.assertEqual(report.source_files_in_scope, 1)
        self.assertEqual(
            [c.rank for c in report.candidates],
            list(range(1, len(report.candidates) + 1)),
        )

    def test_scope_matching_no_tracked_file_raises_value_error_and_cli_exits_2(self):
        from evergreen.till import till

        self.track({"a.py": "def one():\n    pass\n"})

        with self.assertRaises(ValueError):
            till(self.repo, ("evergren",))
        result = self.run_cli("till", "does/not/exist")
        self.assertEqual(result.returncode, 2)
        self.assertIn("matches no tracked file", result.stderr.decode())

    def test_undecodable_tracked_path_fails_closed_with_truncated_warning(self):
        from evergreen import till as module

        self.track({"ok.py": "def one():\n    pass\n"})

        real = module._bounded_git_output

        def listing(repo, arguments):
            if arguments[0] != "ls-files" or "--others" in arguments:
                return real(repo, arguments)
            return (b"ok.py\x00\xff.py\x00", False)

        with mock.patch.object(module, "_bounded_git_output", side_effect=listing):
            report = module.till(self.repo)

        self.assertEqual([c.symbol for c in report.candidates], ["one"])
        self.assertEqual(report.source_files_in_scope, 1)
        self.assertTrue(any("truncated" in item for item in report.warnings))

    def test_tracked_symlink_escaping_the_repo_fails_closed_with_truncated_warning(self):
        import os

        from evergreen.till import till

        outside = Path(self.temporary.name) / "outside.py"
        outside.write_text("def outside():\n    pass\n", encoding="utf-8")
        self.track({"real.py": "def real():\n    pass\n"})
        os.symlink(outside, self.repo / "escaped.py")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)

        report = till(self.repo)

        self.assertEqual([c.symbol for c in report.candidates], ["real"])
        self.assertTrue(any(
            "truncated" in item and "escaped.py" in item for item in report.warnings
        ))

    def test_cross_file_references_outrank_structural_order(self):
        from evergreen.till import till

        self.track({
            "a.py": "class Zeta:\n    pass\ndef omega():\n    pass\n",
            "b.py": "from a import omega\n\nresult = [omega(), omega()]\n",
        })

        report = till(self.repo)

        # omega is named three times outside its file; Zeta never. Usage beats the
        # type-before-callable tier and declaration order.
        self.assertEqual([c.symbol for c in report.candidates][:2], ["omega", "Zeta"])

    def test_candidate_cap_bounds_accumulation_not_just_output(self):
        from evergreen import till as module

        self.track({
            "a.py": "def one():\n    pass\ndef two():\n    pass\n",
            "b.py": "def three():\n    pass\n",
        })

        with mock.patch.object(module, "MAX_GAP_CANDIDATES", 1):
            report = module.till(self.repo)

        self.assertTrue(any("candidates truncated" in item for item in report.warnings))
        # The scan stops once the cap is exceeded instead of accumulating every match.
        self.assertEqual(report.source_files_scanned, 1)
        self.assertEqual(len(report.candidates), 1)

    def test_go_receiver_methods_follow_receiver_and_name_visibility(self):
        from evergreen.till import till

        self.track({"lib.go": (
            "type Receiver struct{}\n"
            "func (r *Receiver) Method() {}\n"
            "func (r *Receiver) hidden() {}\n"
            "type secret struct{}\n"
            "func (s *secret) Loud() {}\n"
        )})

        report = till(self.repo)

        self.assertEqual(
            sorted(c.symbol for c in report.candidates), ["Method", "Receiver"]
        )

    def test_typescript_modules_hide_unexported_functions_and_surface_class_methods(self):
        from evergreen.till import till

        self.track({"api.ts": (
            "export class Bar {\n"
            "  method() {}\n"
            "  private secret() {}\n"
            "  _internal() {}\n"
            "}\n"
            "export const rate = 3;\n"
            "const hidden = 4;\n"
            "function privateHelper() {}\n"
        )})

        report = till(self.repo)

        self.assertEqual(
            sorted((c.symbol, c.kind) for c in report.candidates),
            [("Bar", "class"), ("method", "method"), ("rate", "const")],
        )

    def test_code_inside_a_string_literal_is_not_a_declaration(self):
        from evergreen.till import till

        self.track({"api.py": (
            'DOC = """\n'
            "def phantom():\n"
            "    pass\n"
            '"""\n'
            "def real():\n"
            "    pass\n"
        )})

        report = till(self.repo)

        self.assertEqual([(c.symbol, c.line) for c in report.candidates], [("real", 5)])

    def test_import_alias_lines_produce_no_candidates(self):
        from evergreen.till import till

        self.track({"runner.js": (
            "const fs = require('fs');\n"
            "const path = require('path');\n"
            "function real() {}\n"
        )})

        report = till(self.repo)

        # Regex backtracking once fabricated partial names (`f`, `pat`) from import aliases.
        self.assertEqual([c.symbol for c in report.candidates], ["real"])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "requires FIFO support")
    def test_fifo_swapped_onto_tracked_extensionless_path_does_not_block(self):
        import signal

        from evergreen.till import till

        self.track({"api.py": "def real():\n    pass\n", "tool": "#!/bin/sh\n"})
        (self.repo / "tool").unlink()
        os.mkfifo(self.repo / "tool")

        def timed_out(signum, frame):
            raise AssertionError("till blocked opening a FIFO with no writer")

        previous = signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(10)
        try:
            report = till(self.repo)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous)

        self.assertEqual([c.symbol for c in report.candidates], ["real"])

    def test_extensionless_tracked_symlink_is_surfaced_not_dropped(self):
        from evergreen.till import till

        self.track({
            "api.py": "def real():\n    pass\n",
            "script.txt": "#!/usr/bin/env python3\n",
        })
        os.symlink(self.repo / "script.txt", self.repo / "tool")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)

        report = till(self.repo)

        self.assertEqual([c.symbol for c in report.candidates], ["real"])
        self.assertTrue(any(
            "1 tracked source file(s) outside inventory" in item for item in report.warnings
        ))

    def test_scope_list_beyond_cap_raises_value_error(self):
        from evergreen import till as module

        self.track({"a.py": "def one():\n    pass\n"})

        scope = tuple(f"p{index}" for index in range(module.MAX_GAP_SCOPE_PATHS + 1))
        with self.assertRaises(ValueError):
            module.till(self.repo, scope)

    def test_comment_and_string_mentions_do_not_count_as_references(self):
        from evergreen.till import till

        self.track({
            "a.py": "def alpha():\n    pass\ndef beta():\n    pass\n",
            "b.py": (
                "from a import beta\n"
                "beta()\n"
                "# alpha alpha alpha alpha alpha\n"
                "note = 'alpha alpha alpha alpha'\n"
            ),
        })

        report = till(self.repo)

        symbols = [c.symbol for c in report.candidates]
        self.assertLess(symbols.index("beta"), symbols.index("alpha"))

    def test_fstring_prefixes_are_not_identifier_references(self):
        from evergreen.till import till

        self.track({
            "a.py": "def f():\n    pass\ndef real():\n    pass\n",
            "b.py": (
                "from a import real\n"
                "real()\n"
                "x = f'one'\n"
                "y = f'two'\n"
                "z = f'three'\n"
            ),
        })

        report = till(self.repo)

        symbols = [c.symbol for c in report.candidates]
        self.assertLess(symbols.index("real"), symbols.index("f"))

    def test_repo_argument_inside_the_worktree_is_rejected_not_silently_narrowed(self):
        from evergreen.till import till

        self.track({
            "pkg/a.py": "def one():\n    pass\n",
            "b.py": "def two():\n    pass\n",
        })

        with self.assertRaises(ValueError):
            till(self.repo / "pkg")
        result = self.run_cli("till", "--repo", "pkg", "--json")
        self.assertEqual(result.returncode, 2)
        self.assertIn("repository root", result.stderr.decode())

    def test_go_rust_swift_comments_and_strings_are_not_declarations(self):
        from evergreen.till import till

        self.track({
            "lib.go": "/*\nfunc Phantom() {}\n*/\nfunc Real() {}\n",
            "lib.rs": "// pub fn phantom() {}\npub fn shown() {}\n",
            "lib.swift": (
                '/*\nfunc phantomTwo() {}\n*/\n'
                'let doc = "func phantomThree() {}"\nfunc real() {}\n'
            ),
        })

        report = till(self.repo)

        # `doc` is a real declaration; the masked string body around it is not.
        self.assertEqual(
            sorted((c.symbol, c.path) for c in report.candidates),
            [("Real", "lib.go"), ("doc", "lib.swift"),
             ("real", "lib.swift"), ("shown", "lib.rs")],
        )

    def test_rust_traits_and_statics_are_inventoried(self):
        from evergreen.till import till

        self.track({"shapes.rs": (
            "pub trait Shape {\n"
            "    fn area(&self) -> f64;\n"
            "}\n"
            "pub static MAX: i32 = 10;\n"
            "static hidden: i32 = 3;\n"
        )})

        report = till(self.repo)

        self.assertEqual(
            sorted((c.symbol, c.kind) for c in report.candidates),
            [("MAX", "static"), ("Shape", "trait")],
        )

    def test_swift_actors_are_inventoried(self):
        from evergreen.till import till

        self.track({"counter.swift": "actor Counter {\n}\n"})

        report = till(self.repo)

        self.assertEqual(
            [(c.symbol, c.kind) for c in report.candidates], [("Counter", "actor")]
        )

    def test_go_grouped_declarations_and_interface_methods_are_inventoried(self):
        from evergreen.till import till

        self.track({"lib.go": (
            "type Fooer interface {\n"
            "\tArea() float64\n"
            "\tsecret() int\n"
            "}\n"
            "const (\n"
            "\tMaxSize = 10\n"
            "\tminSize = 1\n"
            ")\n"
            "var (\n"
            "\tDefault = MaxSize\n"
            ")\n"
        )})

        report = till(self.repo)

        self.assertEqual(
            sorted((c.symbol, c.kind) for c in report.candidates),
            [("Area", "method"), ("Default", "var"), ("Fooer", "type"), ("MaxSize", "const")],
        )

    def test_code_after_a_closed_single_line_class_is_not_its_method(self):
        from evergreen.till import till

        self.track({"api.ts": "export class Client { fetch() {} }\n\n  phantom()\n"})

        report = till(self.repo)

        self.assertEqual([c.symbol for c in report.candidates], ["Client"])

    def test_export_aliases_report_the_published_name(self):
        from evergreen.till import till

        self.track({
            "esm.mjs": "function internal() {}\nexport { internal as publicApi };\n",
            "cjs.cjs": "function helper() {}\nexports.renamedApi = helper;\n",
        })

        report = till(self.repo)

        self.assertEqual(
            sorted((c.symbol, c.path) for c in report.candidates),
            [("publicApi", "esm.mjs"), ("renamedApi", "cjs.cjs")],
        )

    def test_untracked_unparsed_uppercase_and_script_files_are_counted(self):
        from evergreen.till import till

        self.track({"api.py": "def real():\n    pass\n"})
        (self.repo / "Main.java").write_text("public class Main {}\n", encoding="utf-8")
        (self.repo / "LOUD.PY").write_text("def loud():\n    pass\n", encoding="utf-8")
        (self.repo / "script").write_text("#!/bin/sh\n", encoding="utf-8")
        (self.repo / "notes").write_text("plain data\n", encoding="utf-8")

        report = till(self.repo)

        self.assertEqual([c.symbol for c in report.candidates], ["real"])
        self.assertTrue(any(
            "3 untracked source file(s) outside inventory" in item for item in report.warnings
        ))

    def test_import_line_mentions_outrank_local_variable_noise(self):
        from evergreen.till import till

        self.track({
            "a.py": "def key():\n    pass\ndef entry():\n    pass\n",
            "b.py": (
                "from a import entry\n"
                "entry()\n"
                "for key in range(3):\n"
                "    items.sort(key=len)\n"
                "    print(key)\n"
            ),
        })

        report = till(self.repo)

        # b.py's `key` locals and kwargs are name collisions, not references to a.key;
        # only the import line vouches for `entry`.
        symbols = [c.symbol for c in report.candidates]
        self.assertLess(symbols.index("entry"), symbols.index("key"))

    def test_escaping_scope_path_raises_value_error_and_cli_exits_2(self):
        from evergreen.till import till

        self.track({"a.py": "def one():\n    pass\n"})

        with self.assertRaises(ValueError):
            till(self.repo, ("../outside",))
        result = self.run_cli("till", "../outside")
        self.assertEqual(result.returncode, 2)
        self.assertTrue(result.stderr.decode().startswith("evergreen:"))


if __name__ == "__main__":
    unittest.main()
