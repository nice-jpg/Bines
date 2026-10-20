"""Ordered policy pipeline around raw conductor tool execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest

from src.agent_messages.tool_results import ToolResult
from src.middleware.execution.dangerous_tool import DangerousToolMiddleware
from src.middleware.execution.operation_notice import OperationNoticeMiddleware
from src.tools.notifications.policy import OperationNoticeService


class ToolExecutionPolicyMiddleware(AgentMiddleware):
    """Validate dangerous operations and announce them before execution."""

    def __init__(
        self,
        *,
        notice_service: OperationNoticeService | None = None,
    ) -> None:
        self._dangerous: DangerousToolMiddleware = DangerousToolMiddleware()
        self._notice: OperationNoticeMiddleware = OperationNoticeMiddleware(service=notice_service)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        return self._dangerous.wrap_tool_call(
            request, lambda inner: self._notice.wrap_tool_call(inner, handler)
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        async def after_dangerous(inner: ToolCallRequest) -> ToolResult:
            return await self._notice.awrap_tool_call(inner, handler)

        return await self._dangerous.awrap_tool_call(request, after_dangerous)
