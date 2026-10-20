"""Keep visual evidence operations sequential without changing tool schemas."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentState
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from pydantic import TypeAdapter

from src.agent_runtime.strategy.base import Strategy

from .state import CollectorState


class CollectorStrategy[StateT: AgentState](Strategy[StateT]):
    def __init__(self, assignment: CollectorState) -> None:
        self._assignment: CollectorState = assignment.copy()

    def _validate_assignment(self, request: ModelRequest[None]) -> None:
        state: CollectorState = TypeAdapter(CollectorState).validate_python(
            request.state.get("collector"), strict=True
        )
        if state != self._assignment:
            raise ValueError("Collector checkpoint does not match the current training assignment")

    def wrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[T]],
    ) -> ModelResponse[T]:
        self._validate_assignment(request)
        return handler(
            request.override(model_settings={**request.model_settings, "parallel_tool_calls": False})
        )

    async def awrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[T]]],
    ) -> ModelResponse[T]:
        self._validate_assignment(request)
        return await handler(
            request.override(model_settings={**request.model_settings, "parallel_tool_calls": False})
        )
