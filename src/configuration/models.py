"""Model factory for the LangChain agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

if TYPE_CHECKING:
    from src.codex import OpenAICodexModel
from src.configuration.errors import ConfigurationError
from src.configuration.manager import ConfigurationManager
from src.configuration.types import ConfigValue, ModelConfig, ModelRule


def get_model_instance(model_rule: ModelRule = None) -> ChatOpenAI | OpenAICodexModel:
    """Create an independent model and transport; unspecified rules use the default group."""

    config: ModelConfig = ConfigurationManager.instance().get_model_for_rule(model_rule)
    metadata = {"prompt_model": config.model, "prompt_affinity": config.affinity}

    if config.provider == "codex":
        from src.codex import create_chat_model

        model = create_chat_model(
            model=config.model,
            reasoning_effort=_option_text(config, "reasoning_effort") or "medium",
            stream_max_retries=_retry_count(config),
        )
        model.metadata = metadata
        return model

    if config.base_url is None or config.api_key is None:
        raise ConfigurationError("API key models require base_url and api_key.")
    return ChatOpenAI(
        base_url=config.base_url,
        api_key=SecretStr(config.api_key),
        model=config.model,
        streaming=False,
        use_responses_api=False,
        metadata=metadata,
    )


def close_model(model: str | BaseChatModel) -> None:
    """Close only model backends that own persistent transport resources."""

    from src.codex import OpenAICodexModel

    if isinstance(model, OpenAICodexModel):
        model.close()


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
