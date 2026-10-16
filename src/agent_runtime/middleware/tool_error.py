"""Convert tool execution exceptions into model-visible tool results."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from src.agent_messages.tool_results import ToolResult


class ToolErrorMiddleware(AgentMiddleware):
    """Return ordinary tool failures to the model instead of aborting the agent."""

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        try:
            return handler(request)
        except Exception as error:
            if _is_langgraph_control_flow(error):
                raise
            return make_tool_error_message(request, error)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        try:
            return await handler(request)
        except Exception as error:
            if _is_langgraph_control_flow(error):
                raise
            return make_tool_error_message(request, error)


def make_tool_error_message(request: ToolCallRequest, error: Exception) -> ToolMessage:
    """Build a concise error result associated with the original tool call."""

    tool_name: str = request.tool_call.get("name") or "unknown_tool"
    tool_call_id: str | None = request.tool_call["id"]
    error_type: str = type(error).__name__
    error_message: str = str(error).strip() or "No error message was provided."
    content: str = (
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
    return isinstance(error, GraphBubbleUp)
