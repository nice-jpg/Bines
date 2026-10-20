"""Reusable page behavior hooks, independent of any application's state."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langgraph.runtime import Runtime

from src.agent_messages.tool_results import ToolResult


class Strategy[StateT: AgentState](AgentMiddleware[StateT, None]):
    """Override only the hooks a page needs; defaults preserve framework behavior."""

    def before_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return None

    async def abefore_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return self.before_model(state, runtime)

    def after_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return None

    async def aafter_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return self.after_model(state, runtime)

    def wrap_model_call[T](
        self, request: ModelRequest[None], handler: Callable[[ModelRequest[None]], ModelResponse[T]]
    ) -> ModelResponse[T]:
        return handler(request)

    async def awrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[T]]],
    ) -> ModelResponse[T]:
        return await handler(request)

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        return handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        return await handler(request)
