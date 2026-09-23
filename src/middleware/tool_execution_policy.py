"""Ordered policy pipeline around raw conductor tool execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest

from .dangerous_tool import DangerousToolMiddleware
from .device_operation_reliability import DeviceOperationReliabilityMiddleware
from .operation_notice import OperationNoticeMiddleware

try:
    from src.tools.event_hub import EventHub
    from src.tools.operation_policy import OperationNoticeService
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from tools.event_hub import EventHub
    from tools.operation_policy import OperationNoticeService


class ToolExecutionPolicyMiddleware(AgentMiddleware):
    """Validate, preflight, announce, execute, and verify tools in a fixed order."""

    def __init__(
        self,
        *,
        event_hub: EventHub,
        notice_service: OperationNoticeService | None = None,
    ) -> None:
        self._dangerous = DangerousToolMiddleware()
        self._reliability = DeviceOperationReliabilityMiddleware(event_hub=event_hub)
        self._notice = OperationNoticeMiddleware(service=notice_service)

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        return self._dangerous.wrap_tool_call(
            request,
            lambda dangerous_request: self._reliability.wrap_tool_call(
                dangerous_request,
                lambda reliable_request: self._notice.wrap_tool_call(
                    reliable_request,
                    handler,
                ),
            ),
        )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        async def after_dangerous(dangerous_request: ToolCallRequest) -> Any:
            async def after_reliability(reliable_request: ToolCallRequest) -> Any:
                async def after_notice(execution_request: ToolCallRequest) -> Any:
                    return await handler(execution_request)

                return await self._notice.awrap_tool_call(reliable_request, after_notice)

            return await self._reliability.awrap_tool_call(dangerous_request, after_reliability)

        return await self._dangerous.awrap_tool_call(request, after_dangerous)


__all__ = ["ToolExecutionPolicyMiddleware"]
