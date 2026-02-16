"""All configuration in one place."""

import os
import yaml

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config.yaml")

# Known provider presets: provider_name -> default base_url
PROVIDER_PRESETS = {
    "openai": None,  # uses OpenAI default
    "openrouter": "https://openrouter.ai/api/v1",
}

# Provider-specific API key env vars (checked before OPENAI_API_KEY fallback)
PROVIDER_KEY_ENV_VARS = {
    "openrouter": "OPENROUTER_API_KEY",
}


def load_config() -> dict:
    """Load config from config.yaml, with env var overrides."""
    with open(CONFIG_PATH, "r") as f:
        config = yaml.safe_load(f)

    # Provider (default: openai)
    config["provider"] = (
        os.environ.get("HERMITCLAW_PROVIDER")
        or config.get("provider", "openai")
    )
    provider = config["provider"]

    # Base URL: env var > config > provider preset
    config["base_url"] = (
        os.environ.get("HERMITCLAW_BASE_URL")
        or config.get("base_url")
        or PROVIDER_PRESETS.get(provider)
    )

    # API key: provider-specific env var > OPENAI_API_KEY > config
    provider_key_var = PROVIDER_KEY_ENV_VARS.get(provider)
    config["api_key"] = (
        (os.environ.get(provider_key_var) if provider_key_var else None)
        or os.environ.get("OPENAI_API_KEY")
        or config.get("api_key")
    )

    # Model
    config["model"] = os.environ.get("HERMITCLAW_MODEL") or config.get("model", "gpt-4o")

    # Defaults for numeric settings
    config.setdefault("thinking_pace_seconds", 45)
    config.setdefault("max_thoughts_in_context", 20)
    config.setdefault("environment_path", "./environment")
    config.setdefault("reflection_threshold", 50)
    config.setdefault("memory_retrieval_count", 3)
    config.setdefault("embedding_model", "text-embedding-3-small")
    config.setdefault("recency_decay_rate", 0.995)

    # Resolve environment_path relative to project root
    project_root = os.path.dirname(os.path.dirname(__file__))
    if not os.path.isabs(config["environment_path"]):
        config["environment_path"] = os.path.join(project_root, config["environment_path"])

    # Validation
    if provider == "custom" and not config.get("base_url"):
        raise ValueError("Provider 'custom' requires base_url in config.yaml or HERMITCLAW_BASE_URL env var")

    return config


# Global config — loaded once, can be updated at runtime
config = load_config()
