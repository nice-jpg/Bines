"""Model adapters consume an initialized immutable configuration service."""

from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from src.application import model
from src.configuration.errors import ConfigurationError
from src.configuration.loader import legacy_configuration, parse_configuration


def test_model_legacy_defaults(configuration_service):
    configuration_service._snapshot = legacy_configuration(
        {
            "MODEL_PROVIDER": "deepseek",
            "DEEPSEEK_BASE_URL": "https://example.test/v1",
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_MODEL": "test-model",
        }
    )
    config = model.load_model_config()
    assert (config.provider, config.model, config.api_key) == ("deepseek", "test-model", "test-key")


def test_build_model_passes_config(monkeypatch, configuration_service):
    configuration_service._snapshot = legacy_configuration(
        {
            "MODEL_PROVIDER": "deepseek",
            "DEEPSEEK_BASE_URL": "https://example.test/v1",
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_MODEL": "test-model",
        }
    )
    constructor = Mock()
    monkeypatch.setattr(model, "ChatOpenAI", constructor)
    assert model.build_model() is constructor.return_value
    constructor.assert_called_once_with(
        base_url="https://example.test/v1",
        api_key=SecretStr("test-key"),
        model="test-model",
        streaming=False,
        use_responses_api=False,
    )


def test_build_codex_model(monkeypatch, configuration_service):
    configuration_service._snapshot = legacy_configuration(
        {
            "MODEL_PROVIDER": "codex",
            "CODEX_MODEL": "gpt-configured",
            "CODEX_REASONING_EFFORT": "high",
            "CODEX_PROMPT_CACHE_KEY": "cache-key",
            "CODEX_STREAM_MAX_RETRIES": "7",
        }
    )
    constructor = Mock()
    monkeypatch.setattr(model, "create_chat_model", constructor)
    assert model.build_model() is constructor.return_value
    constructor.assert_called_once_with(
        model="gpt-configured", reasoning_effort="high", prompt_cache_key="cache-key", stream_max_retries=7
    )


@pytest.mark.parametrize("value", [-1, True, "5"])
def test_invalid_codex_retry_count(value, configuration_service):
    configuration_service._snapshot = parse_configuration(
        {
            "schema_version": 1,
            "model": {
                "providers": {
                    "test": {"provider": "codex", "model": "test", "options": {"stream_max_retries": value}}
                }
            },
        }
    )
    with pytest.raises(ConfigurationError, match="non-negative integer"):
        model.load_model_config()


def test_new_provider_uses_explicit_api_fields(configuration_service):
    configuration_service._snapshot = legacy_configuration(
        {
            "MODEL_PROVIDER": "new_provider",
            "NEW_PROVIDER_BASE_URL": "https://example.test/v1",
            "NEW_PROVIDER_API_KEY": "key",
            "NEW_PROVIDER_MODEL": "model",
        }
    )
    assert model.load_model_config().provider == "new_provider"


def test_missing_provider_fields_rejected():
    with pytest.raises(ConfigurationError):
        legacy_configuration({"MODEL_PROVIDER": "unknown"})
