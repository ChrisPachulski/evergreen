import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from evergreen.judge import resolve_judge_model


class ResolveJudgeModelTest(unittest.TestCase):
    """Config-parsing tests only: resolve_judge_model never invokes the codex binary."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.codex_home = Path(temporary.name)
        patcher = mock.patch.dict(os.environ, {"CODEX_HOME": temporary.name}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        for variable in ("EVERGREEN_JUDGE_MODEL", "EVERGREEN_JUDGE_EFFORT"):
            os.environ.pop(variable, None)

    def write_config(self, text):
        (self.codex_home / "config.toml").write_text(text, encoding="utf-8")

    def test_environment_model_override_wins_over_config(self):
        self.write_config('model = "config-model"\n')
        os.environ["EVERGREEN_JUDGE_MODEL"] = "environment-model"

        self.assertEqual(resolve_judge_model(), ("environment-model", "medium"))

    def test_top_level_config_model_is_returned_without_environment_override(self):
        self.write_config('model = "config-model"\n')

        self.assertEqual(resolve_judge_model(), ("config-model", "medium"))

    def test_nested_config_models_are_ignored(self):
        self.write_config(
            '[projects."example"]\nmodel = "project-model"\n\n'
            '[mcp_servers.example]\nmodel = "server-model"\n'
        )

        self.assertEqual(resolve_judge_model(), (None, "medium"))

    def test_missing_config_returns_no_model_and_medium_effort(self):
        self.assertEqual(resolve_judge_model(), (None, "medium"))

    def test_malformed_config_returns_no_model_and_medium_effort(self):
        self.write_config('model = [\n')

        self.assertEqual(resolve_judge_model(), (None, "medium"))

    def test_non_string_config_model_returns_no_model_and_medium_effort(self):
        self.write_config("model = 42\n")

        self.assertEqual(resolve_judge_model(), (None, "medium"))

    def test_config_effort_is_ignored_in_favor_of_medium(self):
        self.write_config('model = "config-model"\nmodel_reasoning_effort = "ultra"\n')

        self.assertEqual(resolve_judge_model(), ("config-model", "medium"))

    def test_environment_effort_override_wins_over_pinned_default(self):
        os.environ["EVERGREEN_JUDGE_EFFORT"] = "low"

        self.assertEqual(resolve_judge_model(), (None, "low"))


if __name__ == "__main__":
    unittest.main()
