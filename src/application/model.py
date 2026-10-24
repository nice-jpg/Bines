"""Model factory for the LangChain agent."""

from __future__ import annotations

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.codex import OpenAICodexModel, create_chat_model
from src.configuration.errors import ConfigurationError
from src.configuration.manager import ConfigurationManager
from src.configuration.types import ConfigValue, ModelConfig


def build_model() -> ChatOpenAI | OpenAICodexModel:
    """Build the configured model backend from the initialized configuration service."""

    config: ModelConfig = load_model_config()

    if config.provider == "codex":
        return create_chat_model(
            model=config.model,
            reasoning_effort=_option_text(config, "reasoning_effort") or "medium",
            stream_max_retries=_retry_count(config),
        )

    if config.base_url is None or config.api_key is None:
        raise ConfigurationError("API key models require base_url and api_key.")
    return ChatOpenAI(
        base_url=config.base_url,
        api_key=SecretStr(config.api_key),
        model=config.model,
        streaming=False,
        use_responses_api=False,
    )


def close_model(model: ChatOpenAI | OpenAICodexModel) -> None:
    """Close only model backends that own persistent transport resources."""

    if isinstance(model, OpenAICodexModel):
        model.close()


def load_model_config() -> ModelConfig:
    config: ModelConfig = ConfigurationManager.instance().get_model()
    if config.provider == "codex":
        _option_text(config, "reasoning_effort")
        _retry_count(config)
    return config


def _option_text(config: ModelConfig, name: str) -> str | None:
    value: ConfigValue | None = config.options.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("Expected a nonempty Codex option string.")
    return value


def _retry_count(config: ModelConfig) -> int:
    value: ConfigValue = config.options.get("stream_max_retries", 5)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConfigurationError("Codex retries must be a non-negative integer.")
    return value
