from evergreen.judge import resolve_judge_model


def test_environment_model_override_wins_over_config(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text('model = "config-model"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("EVERGREEN_JUDGE_MODEL", "environment-model")

    assert resolve_judge_model() == ("environment-model", "medium")


def test_top_level_config_model_is_returned_without_environment_override(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text('model = "config-model"\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("EVERGREEN_JUDGE_MODEL", raising=False)

    assert resolve_judge_model() == ("config-model", "medium")


def test_nested_config_models_are_ignored(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text(
        '[projects."example"]\nmodel = "project-model"\n\n'
        '[mcp_servers.example]\nmodel = "server-model"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("EVERGREEN_JUDGE_MODEL", raising=False)

    assert resolve_judge_model() == (None, "medium")


def test_missing_config_returns_no_model_and_medium_effort(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("EVERGREEN_JUDGE_MODEL", raising=False)

    assert resolve_judge_model() == (None, "medium")


def test_malformed_config_returns_no_model_and_medium_effort(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text('model = [\n', encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("EVERGREEN_JUDGE_MODEL", raising=False)

    assert resolve_judge_model() == (None, "medium")


def test_non_string_config_model_returns_no_model_and_medium_effort(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text("model = 42\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("EVERGREEN_JUDGE_MODEL", raising=False)

    assert resolve_judge_model() == (None, "medium")


def test_config_effort_is_ignored_in_favor_of_medium(monkeypatch, tmp_path):
    (tmp_path / "config.toml").write_text(
        'model = "config-model"\nmodel_reasoning_effort = "ultra"\n', encoding="utf-8"
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("EVERGREEN_JUDGE_MODEL", raising=False)
    monkeypatch.delenv("EVERGREEN_JUDGE_EFFORT", raising=False)

    assert resolve_judge_model() == ("config-model", "medium")


def test_environment_effort_override_wins_over_pinned_default(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("EVERGREEN_JUDGE_MODEL", raising=False)
    monkeypatch.setenv("EVERGREEN_JUDGE_EFFORT", "low")

    assert resolve_judge_model() == (None, "low")
