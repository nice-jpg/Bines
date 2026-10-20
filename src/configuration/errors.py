"""Configuration failures with caller-supplied, sanitized descriptions."""


class ConfigurationError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ConfigurationNotInitialized(ConfigurationError):
    pass


class ConfigurationKeyError(ConfigurationError):
    pass
