"""One explicitly published configuration snapshot per process."""

from collections.abc import Mapping
from threading import current_thread, main_thread
from typing import ClassVar

from .errors import ConfigurationError, ConfigurationKeyError, ConfigurationNotInitialized
from .loader import load_configuration
from .locking import ReadWriteLock
from .types import ConfigurationSnapshot, ConfigValue, ModelConfig, ModelRule, TraceConfig


class ConfigurationManager:
    _instance: ClassVar[ConfigurationManager | None] = None
    _lock: ClassVar[ReadWriteLock] = ReadWriteLock()
    _snapshot: ConfigurationSnapshot

    def __new__(cls) -> ConfigurationManager:
        raise ConfigurationError("Use ConfigurationManager.initialize() and instance().")

    @classmethod
    def initialize(cls) -> None:
        if current_thread() is not main_thread():
            raise ConfigurationError("Configuration initialization requires the main thread.")
        with cls._lock.read_lock():
            if cls._instance is not None:
                return
        snapshot: ConfigurationSnapshot = load_configuration()
        instance: ConfigurationManager = object.__new__(cls)
        instance._snapshot = snapshot
        with cls._lock.write_lock():
            cls._instance = instance

    @classmethod
    def instance(cls) -> ConfigurationManager:
        with cls._lock.read_lock():
            if cls._instance is None:
                raise ConfigurationNotInitialized(
                    "Initialize configuration before creating application services."
                )
            return cls._instance

    def get_model(self, name: str | None = None) -> ModelConfig:
        with self._lock.read_lock():
            selected: str = _select(self._snapshot.models, name, self._snapshot.model_default)
            return self._snapshot.models[selected]

    def get_model_for_rule(self, model_rule: ModelRule) -> ModelConfig:
        if model_rule not in (None, "coding", "behave"):
            raise ConfigurationError("Model rule must be coding, behave, or None.")
        with self._lock.read_lock():
            name = self._snapshot.model_rules.get(model_rule) if model_rule is not None else None
            selected = _select(self._snapshot.models, name, self._snapshot.model_default)
            return self._snapshot.models[selected]

    def get_channel(self, provider: str, connection: str | None = None) -> Mapping[str, str]:
        _validate_key(provider)
        with self._lock.read_lock():
            if provider not in self._snapshot.channels:
                raise ConfigurationKeyError("Channel provider is not configured.")
            connections: Mapping[str, Mapping[str, str]] = self._snapshot.channels[provider]
            selected: str = _select(connections, connection, self._snapshot.channel_defaults.get(provider))
            return connections[selected]

    def get_trace(self) -> TraceConfig:
        with self._lock.read_lock():
            return self._snapshot.trace

    def get_other(self, *path: str) -> ConfigValue:
        if not path:
            raise ConfigurationError("A configuration path is required.")
        for segment in path:
            _validate_key(segment)
        with self._lock.read_lock():
            value: ConfigValue = self._snapshot.other
            for segment in path:
                if not isinstance(value, Mapping):
                    raise ConfigurationError("Cannot traverse a scalar configuration value.")
                if segment not in value:
                    raise ConfigurationKeyError("Configuration path is not configured.")
                value = value[segment]
            return value


def _validate_key(key: str) -> None:
    if not key.strip():
        raise ConfigurationError("Configuration keys must be nonempty strings.")


def _select[T](values: Mapping[str, T], explicit: str | None, default: str | None) -> str:
    if explicit is not None:
        _validate_key(explicit)
    selected: str | None = explicit if explicit is not None else default
    if selected is not None:
        if selected not in values:
            raise ConfigurationKeyError("Configuration group does not exist.")
        return selected
    if len(values) == 1:
        return next(iter(values))
    if not values:
        raise ConfigurationKeyError("No configuration groups are available.")
    raise ConfigurationError("Multiple configuration groups require an explicit default or name.")
