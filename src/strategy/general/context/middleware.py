"""Cache-friendly conductor message projection and page-manual delivery."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AnyMessage
from langgraph.runtime import Runtime

from src.conductor.state import ConductorState
from src.page.mechanism import PageMechanism
from src.strategy.general.context.compression import DeviceContextCompressionMiddleware
from src.strategy.general.context.manual import PageManualMiddleware


class PageInputMiddleware(AgentMiddleware[ConductorState, None]):
    """Persist the active manual and project history for model requests."""

    state_schema: type[ConductorState] = ConductorState

    def __init__(self, *, mechanism: PageMechanism) -> None:
        self.mechanism: PageMechanism = mechanism
        self._manual: PageManualMiddleware = PageManualMiddleware(mechanism=mechanism)
        self._compression: DeviceContextCompressionMiddleware = DeviceContextCompressionMiddleware()

    def before_model(
        self, state: ConductorState, runtime: Runtime[None]
    ) -> dict[str, list[AnyMessage]] | None:
        return self._manual.before_model(state, runtime)

    async def abefore_model(
        self, state: ConductorState, runtime: Runtime[None]
    ) -> dict[str, list[AnyMessage]] | None:
        return await self._manual.abefore_model(state, runtime)

    def wrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[T]],
    ) -> ModelResponse[T]:
        return self._compression.wrap_model_call(request, handler)

    async def awrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[T]]],
    ) -> ModelResponse[T]:
        return await self._compression.awrap_model_call(request, handler)


__all__: list[str] = ["PageInputMiddleware"]
