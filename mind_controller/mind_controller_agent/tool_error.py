"""Convert tool execution exceptions into model-visible tool results."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage


class ToolErrorMiddleware(AgentMiddleware):
    """Return ordinary tool failures to the model instead of aborting the agent."""

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], ToolMessage | Any],
    ) -> ToolMessage | Any:
        try:
            return handler(request)
        except Exception as error:
            if _is_langgraph_control_flow(error):
                raise
            return make_tool_error_message(request, error)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        try:
            return await handler(request)
        except Exception as error:
            if _is_langgraph_control_flow(error):
                raise
            return make_tool_error_message(request, error)


def make_tool_error_message(request: Any, error: Exception) -> ToolMessage:
    """Build a concise error result associated with the original tool call."""

    tool_call = getattr(request, "tool_call", None)
    call = tool_call if isinstance(tool_call, Mapping) else {}
    tool = getattr(request, "tool", None)
    tool_name = str(call.get("name") or getattr(tool, "name", None) or "unknown_tool")
    tool_call_id = str(call.get("id") or call.get("tool_call_id") or "")
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
