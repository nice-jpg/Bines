"""Model factory for the LangChain agent."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

WORKSPACE_ENV_PATH = Path(__file__).resolve().parents[1] / "workspace" / ".env"


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    api_key: str
    model: str


def build_model() -> ChatOpenAI:
    config = load_model_config()
    return ChatOpenAI(
        base_url=config.base_url,
        api_key=config.api_key,
        model=config.model,
        streaming=False,
    )


def load_model_config(env_path: Path = WORKSPACE_ENV_PATH) -> ModelConfig:
    load_dotenv(env_path, override=False)
    return ModelConfig(
        base_url=_required_env("DEEPSEEK_BASE_URL", "OPENAI_BASE_URL"),
        api_key=_required_env("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYs", "OPENAI_API_KEY"),
        model=_required_env("DEEPSEEK_MODEL", "OPENAI_MODEL"),
    )


def _required_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    joined_names = ", ".join(names)
    raise RuntimeError(f"Missing required model setting in workspace/.env: {joined_names}")
