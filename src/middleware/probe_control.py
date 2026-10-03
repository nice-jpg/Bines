"""Unified conductor page, collector-readiness, and lifecycle control."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import hook_config
from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.tools import StructuredTool

from .collector_readiness import CollectorReadinessMiddleware
from .page_context import PageContextMiddleware, PageContextState
from .probe_lifecycle import ProbeLifecycleMiddleware
from .probe_state import ProbeState

try:
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from page_mechanism import CollectorTrainingRegistry, PageMechanism


class ConductorState(PageContextState, ProbeState):
    """Complete graph state owned by the conductor control plane."""


class ProbeControlMiddleware(AgentMiddleware[ConductorState]):
    """Apply all deterministic probe and page-state rules in one ordered boundary."""

    state_schema = ConductorState

    def __init__(
        self,
        *,
        mechanism: PageMechanism,
        training_registry: CollectorTrainingRegistry,
    ) -> None:
        self.mechanism = mechanism
        self.training_registry = training_registry
        self._lifecycle = ProbeLifecycleMiddleware(mechanism=mechanism, training_registry=training_registry)
        self._page_context = PageContextMiddleware(mechanism=mechanism)
        self._collector_readiness = CollectorReadinessMiddleware(
            mechanism=mechanism,
            training_registry=training_registry,
        )

    def create_tools(self) -> list[StructuredTool]:
        return self._lifecycle.create_tools()

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return self._page_context.wrap_model_call(request, handler)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await self._page_context.awrap_model_call(request, handler)

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: ConductorState, runtime: Any) -> dict[str, Any] | None:
        return self._lifecycle.after_model(state, runtime)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: ConductorState, runtime: Any) -> dict[str, Any] | None:
        return await self._lifecycle.aafter_model(state, runtime)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        return self._lifecycle.wrap_tool_call(
            request,
            lambda lifecycle_request: self._page_context.wrap_tool_call(
                lifecycle_request,
                lambda page_request: self._collector_readiness.wrap_tool_call(page_request, handler),
            ),
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        async def after_lifecycle(lifecycle_request: ToolCallRequest) -> Any:
            async def after_page(page_request: ToolCallRequest) -> Any:
                return await self._collector_readiness.awrap_tool_call(page_request, handler)

            return await self._page_context.awrap_tool_call(lifecycle_request, after_page)

        return await self._lifecycle.awrap_tool_call(request, after_lifecycle)


__all__ = ["ConductorState", "ProbeControlMiddleware"]
