"""Inject explicit Bines settings into nTrace without its environment fallbacks."""

import math
from functools import lru_cache

from nTrace import NTraceClient
from src.configuration.defaults import DEFAULT_TRACE_REQUEST_TIMEOUT, DEFAULT_TRACE_SERVER_URL
from src.configuration.errors import ConfigurationError, ConfigurationKeyError
from src.configuration.manager import ConfigurationManager
from src.configuration.types import ConfigValue, TraceConfig


@lru_cache(maxsize=1)
def trace_client() -> NTraceClient:
    manager: ConfigurationManager = ConfigurationManager.instance()
    config: TraceConfig = manager.get_trace()
    timeout: float = DEFAULT_TRACE_REQUEST_TIMEOUT
    try:
        value: ConfigValue = manager.get_other("trace", "request_timeout")
    except ConfigurationKeyError:
        pass
    else:
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value <= 0
        ):
            raise ConfigurationError("Trace request_timeout must be positive and finite.")
        timeout = float(value)
    return NTraceClient(
        enabled=config.enabled,
        server_url=config.server_url or DEFAULT_TRACE_SERVER_URL,
        request_timeout=timeout,
    )
