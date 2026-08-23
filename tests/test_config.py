"""Config loader tests: valid load, invalid schema, env overrides, no leaks."""

from __future__ import annotations

from pathlib import Path

import pytest

from deepseek_mcp.config.loader import ConfigError, load_config
from tests.conftest import BASE_CONFIG_PATH, make_config_file, make_test_config


def test_loads_valid_yaml(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    assert config.model.name == "deepseek-v4-pro"
    assert config.provider.base_url == "https://api.deepseek.com/anthropic"
    assert config.budget.max_total_tokens_per_run == 3000
    assert config.pricing.per_million_tokens.output == 0.87
    assert config.repository.deny_globs  # non-empty deny rules


def test_default_file_has_expected_shape() -> None:
    config = load_config(path=BASE_CONFIG_PATH, env={})
    assert config.model.name == "deepseek-v4-pro"
    assert config.worker.default_output_detail == "normal"
    assert (
        config.compaction.worker_context_soft_limit_tokens
        < config.compaction.worker_context_hard_limit_tokens
        < config.model.context_window_tokens
    )


def test_rejects_invalid_schema(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="max_api_calls_per_run"):
        make_test_config(tmp_path, {"budget": {"max_api_calls_per_run": "ten"}})


def test_rejects_unknown_version(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="version"):
        make_test_config(tmp_path, {"version": 99})


def test_rejects_invalid_url(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="base_url"):
        make_test_config(tmp_path, {"provider": {"base_url": "not-a-url"}})


def test_rejects_bad_compaction_invariants(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="soft limit"):
        make_test_config(tmp_path, {"compaction": {"worker_context_soft_limit_tokens": 5000}})
    with pytest.raises(ConfigError, match="context window"):
        make_test_config(tmp_path, {"compaction": {"worker_context_hard_limit_tokens": 9000}})
    with pytest.raises(ConfigError, match="final_target_chars"):
        make_test_config(tmp_path, {"compaction": {"final_target_chars": {"normal": 5000}}})


def test_rejects_negative_pricing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="per_million_tokens"):
        make_test_config(tmp_path, {"pricing": {"per_million_tokens": {"output": -1.0}}})


def test_rejects_bad_log_level(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"logging\.level"):
        make_test_config(tmp_path, {"logging": {"level": "loud"}})


def test_honors_deepseek_config_env(tmp_path: Path) -> None:
    path = make_config_file(tmp_path)
    config = load_config(env={"DEEPSEEK_CONFIG": str(path)})
    assert config.config_path == path


def test_honors_base_url_env(tmp_path: Path) -> None:
    path = make_config_file(tmp_path)
    config = load_config(
        path=path, env={"DEEPSEEK_BASE_URL": "https://gateway.example.com/anthropic"}
    )
    assert config.provider.base_url == "https://gateway.example.com/anthropic"


def test_invalid_base_url_env_rejected(tmp_path: Path) -> None:
    path = make_config_file(tmp_path)
    with pytest.raises(ConfigError, match="DEEPSEEK_BASE_URL"):
        load_config(path=path, env={"DEEPSEEK_BASE_URL": "ftp://bad"})


def test_api_key_from_env_not_yaml(tmp_path: Path) -> None:
    path = make_config_file(tmp_path, {"provider": {"api_key": "sk-in-yaml"}})
    config = load_config(path=path, env={"DEEPSEEK_API_KEY": "sk-from-env"})
    assert config.api_key == "sk-from-env"


def test_no_key_in_config(tmp_path: Path) -> None:
    config = make_test_config(tmp_path)
    assert config.api_key is None


def test_error_messages_do_not_leak_key(tmp_path: Path) -> None:
    api_key_value = "sk-super-secret-value"
    bad = make_config_file(tmp_path, {"budget": {"max_total_tokens_per_run": 0}})
    with pytest.raises(ConfigError) as badinfo:
        load_config(path=bad, env={"DEEPSEEK_API_KEY": api_key_value})
    assert api_key_value not in str(badinfo.value)


def test_missing_config_file_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="not found"):
        load_config(env={})


def test_invalid_yaml_errors(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("::: not yaml :::", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML"):
        load_config(path=path, env={})
