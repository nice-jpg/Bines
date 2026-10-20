"""Application defaults shared by loading and third-party configuration adapters."""

from typing import Final

DEFAULT_TRACE_SERVER_URL: Final[str] = "http://127.0.0.1:8765"
DEFAULT_TRACE_REQUEST_TIMEOUT: Final[float] = 30.0
