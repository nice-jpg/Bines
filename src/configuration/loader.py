"""Read configuration once, validate fixed schemas, and recursively freeze values."""

import math
import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from urllib.parse import SplitResult, urlsplit

from dotenv import dotenv_values
from dotenv.variables import parse_variables

from src.common.paths import WORKSPACE_ROOT
from src.serialization.validation import is_string_mapping, is_value_sequence

from .defaults import DEFAULT_TRACE_SERVER_URL
from .errors import ConfigurationError
from .types import ConfigurationSnapshot, ConfigValue, ModelConfig, TraceConfig


def freeze(value: object) -> ConfigValue:
    if isinstance(value, str | bool | int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    if is_string_mapping(value):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if is_value_sequence(value):
        return tuple(freeze(item) for item in value)
    raise ConfigurationError("Configuration contains an unsupported value type.")


def _mapping(value: object) -> Mapping[str, ConfigValue]:
    frozen: ConfigValue = freeze(value)
    if not isinstance(frozen, Mapping):
        raise ConfigurationError("Expected a configuration table.")
    return frozen


def _keys(value: Mapping[str, ConfigValue], allowed: set[str]) -> None:
    if value.keys() - allowed:
        raise ConfigurationError("Unknown field in a fixed configuration table.")


def _text(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("Expected a nonempty configuration string.")
    return value


def _optional_text(value: object) -> str | None:
    return None if value is None else _text(value)


def _url(value: str) -> None:
    try:
        parts: SplitResult = urlsplit(value)
        valid: bool = parts.scheme in {"http", "https"} and bool(parts.hostname)
        valid = valid and (parts.port is None or 0 < parts.port <= 65535)
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError("Expected a valid HTTP(S) server URL.") from None


def parse_configuration(data: Mapping[str, object]) -> ConfigurationSnapshot:
    root: Mapping[str, ConfigValue] = _mapping(data)
    _keys(root, {"schema_version", "model", "channel", "trace", "other"})
    version: ConfigValue | None = root.get("schema_version")
    if type(version) is not int or version != 1:
        raise ConfigurationError("Expected configuration schema_version = 1.")
    model: Mapping[str, ConfigValue] = _mapping(root.get("model", {}))
    _keys(model, {"default", "providers"})
    models: dict[str, ModelConfig] = {}
    providers: Mapping[str, ConfigValue] = _mapping(model.get("providers", {}))
    for name, entry in providers.items():
        _text(name)
        fields: Mapping[str, ConfigValue] = _mapping(entry)
        _keys(fields, {"provider", "model", "base_url", "api_key", "options"})
        provider: str = _text(fields.get("provider")).strip().lower()
        base_url: str | None = _optional_text(fields.get("base_url"))
        api_key: str | None = _optional_text(fields.get("api_key"))
        if provider != "codex" and (base_url is None or api_key is None):
            raise ConfigurationError("API key models require base_url and api_key.")
        if base_url is not None:
            _url(base_url)
        models[name] = ModelConfig(
            provider=provider,
            model=_text(fields.get("model")),
            base_url=base_url,
            api_key=api_key,
            options=_mapping(fields.get("options", {})),
        )
    model_default: str | None = _optional_text(model.get("default"))
    if model_default is not None and model_default not in models:
        raise ConfigurationError("Default model group does not exist.")
    channels: dict[str, Mapping[str, Mapping[str, str]]] = {}
    defaults: dict[str, str] = {}
    channel_root: Mapping[str, ConfigValue] = _mapping(root.get("channel", {}))
    for vendor, entry in channel_root.items():
        _text(vendor)
        group: Mapping[str, ConfigValue] = _mapping(entry)
        _keys(group, {"default", "connections"})
        connections: dict[str, Mapping[str, str]] = {}
        raw_connections: Mapping[str, ConfigValue] = _mapping(group.get("connections", {}))
        for name, connection in raw_connections.items():
            _text(name)
            settings: dict[str, str] = {}
            for key, value in _mapping(connection).items():
                _text(key)
                if not isinstance(value, str):
                    raise ConfigurationError("Channel configuration fields must be strings.")
                settings[key] = value
            connections[name] = MappingProxyType(settings)
        default: str | None = _optional_text(group.get("default"))
        if default is not None:
            if default not in connections:
                raise ConfigurationError("Default channel connection does not exist.")
            defaults[vendor] = default
        channels[vendor] = MappingProxyType(connections)
    trace: Mapping[str, ConfigValue] = _mapping(root.get("trace", {}))
    _keys(trace, {"enabled", "server_url"})
    enabled: ConfigValue = trace.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigurationError("Trace enabled must be boolean.")
    server_url: ConfigValue = trace.get("server_url", DEFAULT_TRACE_SERVER_URL)
    if not isinstance(server_url, str):
        raise ConfigurationError("Trace server_url must be a string.")
    if enabled:
        _url(server_url)
    return ConfigurationSnapshot(
        model_default=model_default,
        models=MappingProxyType(models),
        channel_defaults=MappingProxyType(defaults),
        channels=MappingProxyType(channels),
        trace=TraceConfig(enabled=enabled, server_url=server_url),
        other=_mapping(root.get("other", {})),
    )


def legacy_configuration(values: Mapping[str, str | None]) -> ConfigurationSnapshot:
    """Convert only registered legacy keys, never copy an entire environment."""
    data: dict[str, object] = {"schema_version": 1}
    provider: str | None = values.get("MODEL_PROVIDER")
    if provider:
        provider = provider.strip().lower()
        prefix: str = provider.upper()
        fields: dict[str, object] = {"provider": provider, "model": values.get(f"{prefix}_MODEL")}
        options: dict[str, object] = {}
        if provider == "codex":
            for source, target in (
                ("CODEX_REASONING_EFFORT", "reasoning_effort"),
                ("CODEX_PROMPT_CACHE_KEY", "prompt_cache_key"),
            ):
                if values.get(source):
                    options[target] = values[source]
            retries: str | None = values.get("CODEX_STREAM_MAX_RETRIES")
            if retries:
                try:
                    count: int = int(retries)
                except ValueError:
                    raise ConfigurationError("Codex retries must be a non-negative integer.") from None
                if count < 0:
                    raise ConfigurationError("Codex retries must be a non-negative integer.")
                options["stream_max_retries"] = count
            fields["options"] = options
        else:
            fields["base_url"] = values.get(f"{prefix}_BASE_URL")
            fields["api_key"] = values.get(f"{prefix}_API_KEY")
        data["model"] = {"default": provider, "providers": {provider: fields}}
    feishu: dict[str, str] = {}
    for target, aliases in (
        ("app_id", ("FEISHU_APP_ID", "LARK_APP_ID", "APP_ID")),
        ("app_secret", ("FEISHU_APP_SECRET", "LARK_APP_SECRET", "APP_SECRET")),
        ("log_level", ("FEISHU_LOG_LEVEL",)),
    ):
        for alias in aliases:
            setting: str | None = values.get(alias)
            if setting:
                feishu[target] = setting
                break
    if feishu:
        data["channel"] = {"feishu": {"default": "default", "connections": {"default": feishu}}}
    flag: str = values.get("NTRACE_ENABLED") or "true"
    normalized: str = flag.strip().lower()
    if normalized not in {"0", "1", "false", "true", "no", "yes", "off", "on"}:
        raise ConfigurationError("NTRACE_ENABLED must be boolean.")
    data["trace"] = {
        "enabled": normalized in {"1", "true", "yes", "on"},
        "server_url": values.get("NTRACE_SERVER_URL") or DEFAULT_TRACE_SERVER_URL,
    }
    codex: dict[str, str] = {}
    for source, target in (("AUTH_JSON", "auth_json"), ("CODEX_HOME", "home"), ("CODEX_WS_URL", "ws_url")):
        setting = values.get(source)
        if setting:
            codex[target] = setting
    trace_options: dict[str, ConfigValue] = {}
    timeout: str | None = values.get("NTRACE_REQUEST_TIMEOUT")
    if timeout:
        try:
            seconds: float = float(timeout)
        except ValueError:
            raise ConfigurationError("Trace request_timeout must be positive and finite.") from None
        if not math.isfinite(seconds) or seconds <= 0:
            raise ConfigurationError("Trace request_timeout must be positive and finite.")
        trace_options["request_timeout"] = seconds
    data["other"] = {"codex": codex, "trace": trace_options}
    return parse_configuration(data)


def resolve_legacy_values(
    file_values: Mapping[str, str | None], environment: Mapping[str, str]
) -> Mapping[str, str | None]:
    """Keep dotenv interpolation semantics using the single captured environment."""
    resolved: dict[str, str | None] = {}
    for key, value in file_values.items():
        context: dict[str, str | None] = {**resolved, **environment}
        resolved[key] = (
            None if value is None else "".join(atom.resolve(context) for atom in parse_variables(value))
        )
    resolved.update(environment)
    return resolved


def load_configuration() -> ConfigurationSnapshot:
    path: Path = WORKSPACE_ROOT / "config.toml"
    try:
        if path.exists():
            data: object = tomllib.loads(path.read_text(encoding="utf-8"))
            if not is_string_mapping(data):
                raise ConfigurationError("Expected a configuration document.")
            return parse_configuration(data)
        environment: dict[str, str] = dict(os.environ)
        values: dict[str, str | None] = dict(dotenv_values(WORKSPACE_ROOT / ".env", interpolate=False))
        return legacy_configuration(resolve_legacy_values(values, environment))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        raise ConfigurationError("Cannot read or parse the configuration file.") from None
