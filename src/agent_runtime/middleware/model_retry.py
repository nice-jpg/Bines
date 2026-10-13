"""Provider-independent retry policy for transient model transport failures."""

from __future__ import annotations

from typing import Final

from langchain.agents.middleware import ModelRetryMiddleware
from websockets.exceptions import ConnectionClosed, InvalidStatus

RETRYABLE_HANDSHAKE_STATUS_CODES: Final[frozenset[int]] = frozenset({500, 502, 503, 504})


def is_retryable_model_transport_error(error: BaseException) -> bool:
    """Return whether a failed model call is safe to retry from its checkpoint."""

    if isinstance(error, InvalidStatus):
        return error.response.status_code in RETRYABLE_HANDSHAKE_STATUS_CODES
    return isinstance(error, ConnectionClosed | EOFError | OSError | TimeoutError)


def create_model_retry_middleware() -> ModelRetryMiddleware:
    """Retry transient network failures without depending on a model adapter."""
    return ModelRetryMiddleware(
        max_retries=5,
        retry_on=is_retryable_model_transport_error,
        on_failure="error",
        initial_delay=0.2,
        backoff_factor=2.0,
        max_delay=3.2,
        jitter=True,
    )
