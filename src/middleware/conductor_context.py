"""Cache-friendly conductor message projection and page-manual delivery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse

from .device_context_compression import DeviceContextCompressionMiddleware
from .page_context import PageContextState
from .page_manual import PageManualMiddleware

try:
    from src.page_mechanism import PageMechanism
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from page_mechanism import PageMechanism


class ConductorContextMiddleware(AgentMiddleware[PageContextState]):
    """Persist the active manual and project history for model requests."""

    state_schema = PageContextState

    def __init__(self, *, mechanism: PageMechanism) -> None:
        self.mechanism = mechanism
        self._manual = PageManualMiddleware(mechanism=mechanism)
        self._compression = DeviceContextCompressionMiddleware()

    def before_model(self, state: PageContextState, runtime: Any) -> dict[str, Any] | None:
        return self._manual.before_model(state, runtime)

    async def abefore_model(self, state: PageContextState, runtime: Any) -> dict[str, Any] | None:
        return await self._manual.abefore_model(state, runtime)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return self._compression.wrap_model_call(request, handler)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await self._compression.awrap_model_call(request, handler)


__all__ = ["ConductorContextMiddleware"]
