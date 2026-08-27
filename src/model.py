"""Model factory for the LangChain agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

try:
    # This is the canonical import when launched through mind_controller/run_master.py.
    from codex import OpenAICodexModel, create_chat_model
except ModuleNotFoundError:
    # Also support importing trainer directly from the repository root.
    from src.codex import OpenAICodexModel, create_chat_model

WORKSPACE_ENV_PATH = Path(__file__).resolve().parents[1] / "workspace" / ".env"


@dataclass(frozen=True)
class ModelConfig:
    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    reasoning_effort: str | None = None
    prompt_cache_key: str | None = None
    stream_max_retries: int = 5


def build_model(
    *,
    env_path: Path = WORKSPACE_ENV_PATH,
) -> Any:
    """Build the configured model backend from ``workspace/.env``."""

    config = load_model_config(env_path)

    if config.provider == "codex":
        return create_chat_model(
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            prompt_cache_key=config.prompt_cache_key,
            stream_max_retries=config.stream_max_retries,
        )

    return ChatOpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        streaming=False,
        use_responses_api=False,
    )


def close_model(model: ChatOpenAI | OpenAICodexModel) -> None:
    """Close only model backends that own persistent transport resources."""

    if isinstance(model, OpenAICodexModel):
        model.close()


def load_model_config(env_path: Path = WORKSPACE_ENV_PATH) -> ModelConfig:
    load_dotenv(env_path, override=False)
    provider = _required_env("MODEL_PROVIDER").strip().lower()

    if provider == "codex":
        return ModelConfig(
            provider=provider,
            model=_required_env("CODEX_MODEL"),
            reasoning_effort=_optional_env("CODEX_REASONING_EFFORT") or "medium",
            prompt_cache_key=_optional_env("CODEX_PROMPT_CACHE_KEY"),
            stream_max_retries=_optional_non_negative_int("CODEX_STREAM_MAX_RETRIES", default=5),
        )
    if provider == "deepseek":
        return ModelConfig(
            provider=provider,
            base_url=_required_env("DEEPSEEK_BASE_URL"),
            api_key=_required_env("DEEPSEEK_API_KEY"),
            model=_required_env("DEEPSEEK_MODEL"),
        )
    if provider == "openai":
        return ModelConfig(
            provider=provider,
            base_url=_required_env("OPENAI_BASE_URL"),
            api_key=_required_env("OPENAI_API_KEY"),
            model=_required_env("OPENAI_MODEL"),
        )
    raise RuntimeError(
        "Unsupported MODEL_PROVIDER in workspace/.env: "
        f"{provider!r}. Expected one of: codex, deepseek, openai"
    )


def _required_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    joined_names = ", ".join(names)
    raise RuntimeError(f"Missing required model setting in workspace/.env: {joined_names}")


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None


def _optional_non_negative_int(name: str, *, default: int) -> int:
    value = _optional_env(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise RuntimeError(f"{name} must be a non-negative integer.") from error
    if parsed < 0:
        raise RuntimeError(f"{name} must be a non-negative integer.")
    return parsed
