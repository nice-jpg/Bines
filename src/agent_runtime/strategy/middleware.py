"""Delegate each hook to the strategy selected by the owning Engine."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langgraph.runtime import Runtime
from pydantic import TypeAdapter

from src.agent_messages.tool_results import ToolResult

from .base import Strategy
from .registry import StrategyRegistry


class StrategyDispatchMiddleware[StateT: AgentState](AgentMiddleware[StateT, None]):
    def __init__(
        self,
        *,
        state_schema: type[StateT],
        registry: StrategyRegistry[StateT],
        select_strategy: Callable[[StateT], str],
    ) -> None:
        self.state_schema: type[StateT] = state_schema
        self._adapter: TypeAdapter[StateT] = TypeAdapter(state_schema)
        self._registry: StrategyRegistry[StateT] = registry
        self._select_strategy: Callable[[StateT], str] = select_strategy

    def _read_state(self, value: object) -> StateT:
        return self._adapter.validate_python(value, strict=True)

    def _resolve_strategy(self, state: StateT) -> Strategy[StateT]:
        return self._registry.get(self._select_strategy(state))

    def before_agent(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return self._registry.initial_state(state) or None

    async def abefore_agent(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return self.before_agent(state, runtime)

    def before_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return self._resolve_strategy(state).before_model(state, runtime)

    async def abefore_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return await self._resolve_strategy(state).abefore_model(state, runtime)

    def after_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return self._resolve_strategy(state).after_model(state, runtime)

    async def aafter_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        return await self._resolve_strategy(state).aafter_model(state, runtime)

    def wrap_model_call[T](
        self, request: ModelRequest[None], handler: Callable[[ModelRequest[None]], ModelResponse[T]]
    ) -> ModelResponse[T]:
        strategy: Strategy[StateT] = self._resolve_strategy(self._read_state(request.state))
        return strategy.wrap_model_call(request, handler)

    async def awrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[T]]],
    ) -> ModelResponse[T]:
        strategy: Strategy[StateT] = self._resolve_strategy(self._read_state(request.state))
        return await strategy.awrap_model_call(request, handler)

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        strategy: Strategy[StateT] = self._resolve_strategy(self._read_state(request.state))
        return strategy.wrap_tool_call(request, handler)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        strategy: Strategy[StateT] = self._resolve_strategy(self._read_state(request.state))
        return await strategy.awrap_tool_call(request, handler)
