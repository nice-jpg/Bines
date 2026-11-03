"""Immutable configuration values; nested mappings are frozen by the loader."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

type ModelRule = Literal["coding", "behave"] | None

type ConfigValue = str | bool | int | float | tuple[ConfigValue, ...] | Mapping[str, ConfigValue]


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class ModelConfig:
    provider: str
    model: str
    base_url: str | None
    api_key: str | None
    options: Mapping[str, ConfigValue]


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class TraceConfig:
    enabled: bool
    server_url: str


@dataclass(frozen=True, slots=True, kw_only=True, repr=False)
class ConfigurationSnapshot:
    model_default: str | None
    model_rules: Mapping[str, str]
    models: Mapping[str, ModelConfig]
    channel_defaults: Mapping[str, str]
    channels: Mapping[str, Mapping[str, Mapping[str, str]]]
    trace: TraceConfig
    other: Mapping[str, ConfigValue]
