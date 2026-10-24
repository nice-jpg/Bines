"""Compose strategy middleware and expose execution-scope lifecycle hooks."""

from collections.abc import Awaitable, Callable, Sequence
from functools import partial
from typing import cast

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import MessageLikeRepresentation
from langgraph.graph.message import add_messages
from langgraph.runtime import Runtime

from src.agent_messages.tool_results import ToolResult
from src.agent_runtime.types.framework import RuntimeMiddleware


class Strategy[StateT: AgentState](AgentMiddleware[StateT, None]):
    """Route hooks through admission middleware before functional middleware."""

    def __init__(
        self, *, constraints: Sequence[RuntimeMiddleware], features: Sequence[RuntimeMiddleware]
    ) -> None:
        self.middlewares: tuple[RuntimeMiddleware, ...] = (*constraints, *features)

    def activate(self, state: StateT) -> dict[str, object] | None:
        """Idempotently bind owned state before each selected model boundary.

        Return complete state namespaces, without messages or graph control flow.
        """
        return None

    def deactivate(self, state: StateT) -> dict[str, object] | None:
        """Idempotently release owned state while this strategy is not selected."""
        return None

    @staticmethod
    def _merge(state: StateT, accumulated: dict[str, object], update: dict[str, object]) -> StateT:
        messages: object = update.get("messages")
        if messages is not None:
            accumulated["messages"] = [
                *cast(list[MessageLikeRepresentation], accumulated.get("messages", [])),
                *cast(list[MessageLikeRepresentation], messages),
            ]
        accumulated.update({key: value for key, value in update.items() if key != "messages"})
        projected: dict[str, object] = {**state, **accumulated}
        if "messages" in accumulated:
            projected["messages"] = add_messages(
                state.get("messages", []), cast(list[MessageLikeRepresentation], accumulated["messages"])
            )
        return cast(StateT, projected)

    def before_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        update: dict[str, object] = {}
        current: StateT = state
        for layer in self.middlewares:
            result: dict[str, object] | None = layer.before_model(current, runtime)
            if result:
                current = self._merge(state, update, result)
                if result.get("jump_to"):
                    break
        return update or None

    async def abefore_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        update: dict[str, object] = {}
        current: StateT = state
        for layer in self.middlewares:
            result: dict[str, object] | None = await layer.abefore_model(current, runtime)
            if result:
                current = self._merge(state, update, result)
                if result.get("jump_to"):
                    break
        return update or None

    def after_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        update: dict[str, object] = {}
        current: StateT = state
        for layer in self.middlewares:
            result: dict[str, object] | None = layer.after_model(current, runtime)
            if result:
                current = self._merge(state, update, result)
                if result.get("jump_to"):
                    break
        return update or None

    async def aafter_model(self, state: StateT, runtime: Runtime[None]) -> dict[str, object] | None:
        update: dict[str, object] = {}
        current: StateT = state
        for layer in self.middlewares:
            result: dict[str, object] | None = await layer.aafter_model(current, runtime)
            if result:
                current = self._merge(state, update, result)
                if result.get("jump_to"):
                    break
        return update or None

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        for layer in reversed(self.middlewares):
            if type(layer).wrap_tool_call is not AgentMiddleware.wrap_tool_call:
                handler = partial(layer.wrap_tool_call, handler=handler)
        return handler(request)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        for layer in reversed(self.middlewares):
            if type(layer).awrap_tool_call is not AgentMiddleware.awrap_tool_call:
                handler = partial(layer.awrap_tool_call, handler=handler)
        return await handler(request)

    def wrap_model_call[T](
        self, request: ModelRequest[None], handler: Callable[[ModelRequest[None]], ModelResponse[T]]
    ) -> ModelResponse[T]:
        for layer in reversed(self.middlewares):
            if type(layer).wrap_model_call is not AgentMiddleware.wrap_model_call:
                handler = partial(layer.wrap_model_call, handler=handler)
        return handler(request)

    async def awrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[T]]],
    ) -> ModelResponse[T]:
        for layer in reversed(self.middlewares):
            if type(layer).awrap_model_call is not AgentMiddleware.awrap_model_call:
                handler = partial(layer.awrap_model_call, handler=handler)
        return await handler(request)
