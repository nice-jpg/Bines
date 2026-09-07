"""Automatically announce external operations at the tool execution boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.tools import BaseTool

if __package__ and __package__.startswith("src."):
    from src.middleware.page_context import PAGE_CONTEXT_PATH
    from src.middleware.tool_io import ToolInvocation
    from src.tools.operation_policy import (
        OperationNoticeService,
        external_operation_policy_from_metadata,
        operation_notice_service_from_metadata,
    )
else:  # Supports running tests and python src/run_agent.py with src on sys.path.
    from middleware.page_context import PAGE_CONTEXT_PATH
    from middleware.tool_io import ToolInvocation
    from tools.operation_policy import (
        OperationNoticeService,
        external_operation_policy_from_metadata,
        operation_notice_service_from_metadata,
    )


class OperationNoticeMiddleware(AgentMiddleware):
    """Notify through the configured service immediately before real side effects."""

    def __init__(
        self,
        tools: Sequence[BaseTool] | None = None,
        *,
        service: OperationNoticeService | None = None,
    ) -> None:
        """Bind an explicit service; tool discovery remains a compatibility adapter."""

        self.service = service if service is not None else _notice_service(tools or ())

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        self._notify(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        self._notify(request)
        return await handler(request)

    def _notify(self, request: ToolCallRequest) -> None:
        invocation = ToolInvocation.from_request(request)
        policy = external_operation_policy_from_metadata(invocation.tool_metadata)
        if self.service is None or policy is None:
            return

        next_path = ""
        if policy.next_path_argument is not None:
            next_path = str(invocation.args.get(policy.next_path_argument) or "").strip()
        next_page = next_path.rsplit("/", 1)[-1] if next_path else ""
        reason = _operation_reason(invocation.args, policy.reason)
        self.service.notify_user(
            operation=invocation.name,
            current_path=str(invocation.state.get(PAGE_CONTEXT_PATH) or ""),
            reason=reason,
            next_page=next_page,
            next_path=next_path,
        )


def _notice_service(tools: Sequence[BaseTool]) -> OperationNoticeService | None:
    for tool in tools:
        service = operation_notice_service_from_metadata(tool.metadata)
        if service is not None:
            return service
    return None


def _operation_reason(args: Mapping[str, Any], fallback: str) -> str:
    justification = args.get("justification")
    if isinstance(justification, Mapping):
        reason = str(justification.get("reason") or "").strip()
        if reason:
            return reason
    return fallback


__all__ = ["OperationNoticeMiddleware"]
