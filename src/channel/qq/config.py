"""QQ configuration loaded from the shared configuration service."""

from dataclasses import dataclass

from src.configuration.errors import ConfigurationError
from src.configuration.manager import ConfigurationManager


@dataclass(frozen=True, repr=False)
class QQConfig:
    app_id: str
    app_secret: str
    sandbox: bool = False
    public_messages: bool = True
    public_guild_messages: bool = True
    direct_message: bool = True

    def __post_init__(self) -> None:
        if not self.app_id.strip() or not self.app_secret.strip():
            raise ConfigurationError("QQ app_id and app_secret are required")
        if not (self.public_messages or self.public_guild_messages or self.direct_message):
            raise ConfigurationError("At least one QQ message intent is required")


def load_qq_config(connection: str | None = None) -> QQConfig:
    values = ConfigurationManager.instance().get_channel("qq", connection)
    flags: dict[str, bool] = {}
    for key, default in (
        ("sandbox", "false"),
        ("public_messages", "true"),
        ("public_guild_messages", "true"),
        ("direct_message", "true"),
    ):
        value = values.get(key, default).strip().lower()
        if value not in {"true", "false"}:
            raise ConfigurationError(f"QQ {key} must be true or false")
        flags[key] = value == "true"
    return QQConfig(values.get("app_id", ""), values.get("app_secret", ""), **flags)
