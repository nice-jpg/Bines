"""Feishu channel configuration loaded from the workspace environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from src.paths import WORKSPACE_ENV_PATH


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    log_level: str = "INFO"


def load_feishu_config() -> FeishuConfig:
    """Load Feishu channel settings from ``workspace/.env``."""

    load_dotenv(WORKSPACE_ENV_PATH, override=False)
    return FeishuConfig(
        app_id=_required_env("FEISHU_APP_ID", "LARK_APP_ID", "APP_ID"),
        app_secret=_required_env("FEISHU_APP_SECRET", "LARK_APP_SECRET", "APP_SECRET"),
        log_level=os.getenv("FEISHU_LOG_LEVEL", "INFO"),
    )


def _required_env(*names: str) -> str:
    for name in names:
        value: str | None = os.getenv(name)
        if value:
            return value
    joined_names: str = ", ".join(names)
    raise RuntimeError(f"Missing required Feishu setting in workspace/.env: {joined_names}")
