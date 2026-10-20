"""Validate Feishu-specific fields from the configuration service."""

from collections.abc import Mapping
from dataclasses import dataclass

from src.configuration.errors import ConfigurationError
from src.configuration.manager import ConfigurationManager


@dataclass(frozen=True, repr=False)
class FeishuConfig:
    app_id: str
    app_secret: str
    log_level: str = "INFO"


def load_feishu_config() -> FeishuConfig:
    values: Mapping[str, str] = ConfigurationManager.instance().get_channel("feishu")
    for key in ("app_id", "app_secret"):
        if not values.get(key, "").strip():
            raise ConfigurationError("Missing required Feishu setting: " + key)
    level: str = values.get("log_level", "INFO").upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigurationError("Invalid Feishu log_level.")
    return FeishuConfig(app_id=values["app_id"], app_secret=values["app_secret"], log_level=level)
