"""Convert tool execution exceptions into model-visible tool results."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

try:
    from src.middleware.tool_io import ToolInvocation
except ModuleNotFoundError:
    from middleware.tool_io import ToolInvocation


class ToolErrorMiddleware(AgentMiddleware):
    """Return ordinary tool failures to the model instead of aborting the agent."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        try:
            return handler(request)
        except Exception as error:
            if _is_langgraph_control_flow(error):
                raise
            return make_tool_error_message(request, error)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        try:
            return await handler(request)
        except Exception as error:
            if _is_langgraph_control_flow(error):
                raise
            return make_tool_error_message(request, error)


def make_tool_error_message(request: ToolCallRequest, error: Exception) -> ToolMessage:
    """Build a concise error result associated with the original tool call."""

    invocation = ToolInvocation.from_request(request)
    tool_name = invocation.name or "unknown_tool"
    tool_call_id = invocation.call_id
    error_type = type(error).__name__
    error_message = str(error).strip() or "No error message was provided."
    content = (
        f"Tool '{tool_name}' failed with {error_type}: {error_message}\n"
        "Review the failure, correct the tool arguments or choose another action, and continue."
    )
    return ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        name=tool_name,
        status="error",
        additional_kwargs={
            "error_type": error_type,
            "tool_name": tool_name,
        },
    )


def _is_langgraph_control_flow(error: Exception) -> bool:
    for error_type in type(error).__mro__:
        if (
            error_type.__name__ in {"GraphBubbleUp", "GraphInterrupt"}
            and error_type.__module__.startswith("langgraph.")
        ):
            return True
    return False
