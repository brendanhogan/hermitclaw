"""Tests for multi-provider config loading."""

import os
import pytest


def test_default_provider_is_openai(tmp_path, monkeypatch):
    """Config without provider field should default to openai."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("model: gpt-4.1\napi_key: test-key\n")
    monkeypatch.setattr("hermitclaw.config.CONFIG_PATH", str(config_file))

    from hermitclaw.config import load_config
    cfg = load_config()
    assert cfg["provider"] == "openai"


def test_openrouter_provider_sets_base_url(tmp_path, monkeypatch):
    """OpenRouter provider should auto-set base_url."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("provider: openrouter\nmodel: openai/gpt-4.1\napi_key: or-key\n")
    monkeypatch.setattr("hermitclaw.config.CONFIG_PATH", str(config_file))

    from hermitclaw.config import load_config
    cfg = load_config()
    assert cfg["base_url"] == "https://openrouter.ai/api/v1"


def test_openrouter_api_key_env_var(tmp_path, monkeypatch):
    """OPENROUTER_API_KEY should be used for openrouter provider."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("provider: openrouter\nmodel: openai/gpt-4.1\n")
    monkeypatch.setattr("hermitclaw.config.CONFIG_PATH", str(config_file))
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-env-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    from hermitclaw.config import load_config
    cfg = load_config()
    assert cfg["api_key"] == "or-env-key"


def test_custom_provider_requires_base_url(tmp_path, monkeypatch):
    """Custom provider without base_url should raise ValueError."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("provider: custom\nmodel: llama3\napi_key: key\n")
    monkeypatch.setattr("hermitclaw.config.CONFIG_PATH", str(config_file))

    from hermitclaw.config import load_config
    with pytest.raises(ValueError, match="base_url"):
        load_config()


def test_env_var_overrides(tmp_path, monkeypatch):
    """HERMITCLAW_PROVIDER and HERMITCLAW_BASE_URL should override config."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("provider: openai\nmodel: gpt-4.1\napi_key: key\n")
    monkeypatch.setattr("hermitclaw.config.CONFIG_PATH", str(config_file))
    monkeypatch.setenv("HERMITCLAW_PROVIDER", "custom")
    monkeypatch.setenv("HERMITCLAW_BASE_URL", "http://localhost:11434/v1")

    from hermitclaw.config import load_config
    cfg = load_config()
    assert cfg["provider"] == "custom"
    assert cfg["base_url"] == "http://localhost:11434/v1"
