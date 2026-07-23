"""Retry policy for transient Codex model transport failures."""

from __future__ import annotations

from langchain.agents.middleware import ModelRetryMiddleware
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .codex_oauth import OpenAICodexModel

RETRYABLE_HANDSHAKE_STATUS_CODES = frozenset({500, 502, 503, 504})


def is_retryable_model_transport_error(error: BaseException) -> bool:
    """Return whether a failed model call is safe to retry from its checkpoint."""

    if isinstance(error, InvalidStatus):
        return error.response.status_code in RETRYABLE_HANDSHAKE_STATUS_CODES
    return isinstance(error, (ConnectionClosed, EOFError, OSError, TimeoutError))


def create_codex_model_retry_middleware(
    model: str | BaseChatModel,
) -> list[AgentMiddleware]:
    """Build the retry layer only for the Codex WebSocket model."""

    if not isinstance(model, OpenAICodexModel):
        return []
    return [
        ModelRetryMiddleware(
            max_retries=model.stream_max_retries,
            retry_on=is_retryable_model_transport_error,
            on_failure="error",
            initial_delay=0.2,
            backoff_factor=2.0,
            max_delay=3.2,
            jitter=True,
        )
    ]
