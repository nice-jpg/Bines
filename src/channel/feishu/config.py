"""Feishu channel configuration loaded from the workspace environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

WORKSPACE_ENV_PATH = Path(__file__).resolve().parents[3] / "workspace" / ".env"


@dataclass(frozen=True)
class FeishuConfig:
    app_id: str
    app_secret: str
    log_level: str = "INFO"


def load_feishu_config(env_path: Path = WORKSPACE_ENV_PATH) -> FeishuConfig:
    """Load Feishu channel settings from ``workspace/.env``."""

    load_dotenv(env_path, override=False)
    return FeishuConfig(
        app_id=_required_env("FEISHU_APP_ID", "LARK_APP_ID", "APP_ID"),
        app_secret=_required_env("FEISHU_APP_SECRET", "LARK_APP_SECRET", "APP_SECRET"),
        log_level=os.getenv("FEISHU_LOG_LEVEL", "INFO"),
    )


def _required_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    joined_names = ", ".join(names)
    raise RuntimeError(f"Missing required Feishu setting in workspace/.env: {joined_names}")
