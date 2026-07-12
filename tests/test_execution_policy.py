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
    def classify(self, argv):
        from evergreen.execution_policy import classify_command

        return classify_command(argv)

    def test_allows_known_repository_test_drivers_without_executing_them(self):
        commands = (
            ["pytest", "-q"],
            ["/usr/bin/python3", "-m", "unittest", "discover"],
            ["npm", "test"],
            ["npm", "run", "test:unit"],
            ["pnpm", "test"],
            ["yarn", "test"],
            ["cargo", "test"],
            ["go", "test", "./..."],
            ["swift", "test"],
            ["dotnet", "test"],
            ["xcodebuild", "test", "-scheme", "App"],
            ["make", "test"],
            ["./gradlew", "test"],
            ["bundle", "exec", "rspec"],
        )

        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.classify(command), "allowed")

    def test_bounded_timeout_wrapper_preserves_nested_classification(self):
        self.assertEqual(self.classify(["timeout", "30", "pytest", "-q"]), "allowed")
        self.assertEqual(self.classify(["gtimeout", "900", "cargo", "test"]), "allowed")
        for value in ("0", "901", "none", "30s"):
            with self.subTest(value=value):
                self.assertEqual(self.classify(["timeout", value, "pytest"]), "inconclusive")

    def test_timeout_accepts_ascii_decimal_only_and_never_leaks_conversion_errors(self):
        for value in ("٣٠", "²", "３０", "+30", "9" * 4096):
            with self.subTest(value=value[:20]):
                self.assertEqual(self.classify(["timeout", value, "pytest"]), "inconclusive")

    def test_refuses_shell_indirection_and_metacharacters_without_parsing_shell(self):
        commands = (
            ["sh", "-c", "pytest"],
            ["bash", "-lc", "pytest"],
            ["pytest", ";", "rm", "-rf", "."],
            ["pytest", "&&", "echo"],
            ["pytest", "$(id)"],
            ["pytest", "`id`"],
            ["pytest", "a|b"],
            ["pytest", "redirect>file"],
            ["pytest\nrm"],
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.classify(command), "refused")
        self.assertEqual(self.classify(["pytest -q"]), "inconclusive")

    def test_refuses_privilege_deployment_upload_push_portal_and_cleanup(self):
        commands = (
            ["sudo", "pytest"],
            ["doas", "make", "test"],
            ["env", "sudo", "pytest"],
            ["command", "bash", "-c", "pytest"],
            ["git", "push", "origin", "main"],
            ["git", "clean", "-fdx"],
            ["git", "reset", "--hard"],
            ["npm", "publish"],
            ["cargo", "publish"],
            ["twine", "upload", "dist/*"],
            ["docker", "push", "image"],
            ["kubectl", "apply", "-f", "deploy.yml"],
            ["terraform", "destroy"],
            ["gh", "release", "create", "v1"],
            ["fastlane", "upload_to_testflight"],
            ["xcrun", "notarytool", "submit", "App.zip"],
            ["rm", "-rf", "scratch"],
            ["make", "clean"],
            ["./scripts/deploy.sh", "staging"],
            ["npm", "run", "upload:staging"],
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.classify(command), "refused")

    def test_dangerous_components_cannot_hide_inside_known_test_argv(self):
        commands = (
            ["pytest", "test:deploy"],
            ["pytest", "--upload"],
            ["pytest", "test_cleanup"],
            ["pytest", "--artifacts-release"],
            ["make", "test", "deploy"],
            ["npm", "test", "--publish-results"],
            ["cargo", "test", "predeploy"],
            ["pytest", "--deployment-mode"],
            ["pytest", "cleanupNow"],
            ["timeout", "30", "pytest", "test:deploy"],
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.classify(command), "refused")

    def test_shell_privilege_and_destructive_components_cannot_hide_in_flags_or_args(self):
        commands = (
            ["pytest", "--sudo"],
            ["pytest", "test:sudo"],
            ["pytest", "--rm"],
            ["pytest", "test:mkfs"],
            ["pytest", "--bash"],
            ["pytest", "runner:powershell"],
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.classify(command), "refused")

    def test_executable_parent_directories_and_source_path_prose_are_not_operations(self):
        commands = (
            ["/tmp/release/pytest", "-q"],
            ["pytest", "tests/test_release_notes.py"],
            ["pytest", "test_push_notification.py"],
            ["python3", "-m", "pytest", "/tmp/upload/tests/test_release_notes.py"],
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.classify(command), "allowed")

    def test_common_test_flags_do_not_match_short_command_name_fragments(self):
        commands = (
            ["pytest", "--showlocals"],
            ["pytest", "--setup-show"],
            ["pytest", "--suppress-no-test-exit-code"],
            ["pytest", "--summary"],
            ["go", "test", "-shuffle=on"],
            ["cargo", "test", "--", "--show-output"],
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.classify(command), "allowed")

    def test_network_secret_and_unavailable_isolation_are_inconclusive(self):
        commands = (
            ["pytest", "--network"],
            ["pytest", "--allow-net"],
            ["pytest", "https://example.com/case"],
            ["pytest", "--api-key=secret"],
            ["pytest", "--github-token"],
            ["pytest", "TOKEN=secret"],
            ["env", "TOKEN=secret", "pytest"],
            ["docker", "run", "test-image"],
            ["podman", "run", "test-image"],
            ["sandbox-exec", "-p", "profile", "pytest"],
            ["project-test-tool", "--safe"],
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertEqual(self.classify(command), "inconclusive")

    def test_invalid_or_empty_argv_is_inconclusive(self):
        class StringSubclass(str):
            pass

        for argv in (
            None, [], "pytest", [1], [""], ["timeout", "30"],
            [StringSubclass("pytest")], ["pytest", StringSubclass("--upload")],
        ):
            with self.subTest(argv=argv):
                self.assertEqual(self.classify(argv), "inconclusive")

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
