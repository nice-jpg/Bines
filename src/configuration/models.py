"""Model factory for the LangChain agent."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from src.configuration.errors import ConfigurationError
from src.configuration.manager import ConfigurationManager
from src.configuration.types import ConfigValue, ModelConfig, ModelRule
from src.providers.deepseek import ChatDeepSeek


def get_model_instance(model_rule: ModelRule = None) -> BaseChatModel:
    """Create an independent model and transport; unspecified rules use the default group."""

    config: ModelConfig = ConfigurationManager.instance().get_model_for_rule(model_rule)
    builder = _MODEL_BUILDERS.get(config.provider, _MODEL_BUILDERS["openai"])
    model = builder(config)
    model.metadata = {"prompt_model": config.model, "prompt_affinity": config.affinity}
    return model


def _build_codex(config: ModelConfig) -> BaseChatModel:
    from src.providers.codex import create_chat_model

    return create_chat_model(
        model=config.model,
        reasoning_effort=_option_text(config, "reasoning_effort") or "medium",
        stream_max_retries=_retry_count(config),
    )


def _build_api_model(model_class: type[ChatOpenAI], config: ModelConfig) -> ChatOpenAI:
    if config.base_url is None or config.api_key is None:
        raise ConfigurationError("API key models require base_url and api_key.")
    return model_class(
        base_url=config.base_url,
        api_key=SecretStr(config.api_key),
        model=config.model,
        streaming=False,
        use_responses_api=False,
    )


_MODEL_BUILDERS: dict[str, Callable[[ModelConfig], BaseChatModel]] = {
    "codex": _build_codex,
    "deepseek": partial(_build_api_model, ChatDeepSeek),
    "openai": partial(_build_api_model, ChatOpenAI),
}


def close_model(model: str | BaseChatModel) -> None:
    """Close only model backends that own persistent transport resources."""

    from src.providers.codex import OpenAICodexModel

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
