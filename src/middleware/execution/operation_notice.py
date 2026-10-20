"""Automatically announce external operations at the tool execution boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest

from src.agent_messages.tool_results import ToolResult
from src.common.utils.json import JsonValue
from src.conductor.state import current_visit
from src.middleware.tool_io import ToolInvocation
from src.tools.notifications.policy import (
    ExternalOperationPolicy,
    OperationNoticeService,
    external_operation_policy_from_metadata,
)


class OperationNoticeMiddleware(AgentMiddleware):
    """Notify through the configured service immediately before real side effects."""

    def __init__(self, *, service: OperationNoticeService | None = None) -> None:
        self.service: OperationNoticeService | None = service

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        self._notify(request)
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        self._notify(request)
        return await handler(request)

    def _notify(self, request: ToolCallRequest) -> None:
        invocation: ToolInvocation = ToolInvocation.from_request(request)
        policy: ExternalOperationPolicy | None = external_operation_policy_from_metadata(
            invocation.tool_metadata
        )
        if self.service is None or policy is None:
            return

        next_path: str = ""
        if policy.next_path_argument is not None:
            target: JsonValue = invocation.args.get(policy.next_path_argument)
            if isinstance(target, str):
                next_path = target.strip()
        next_page: str = next_path.rsplit("/", 1)[-1] if next_path else ""
        reason: str = _operation_reason(invocation.args, policy.reason)
        self.service.notify_user(
            operation=invocation.name,
            current_path=str(current_visit(invocation.state).get("path") or ""),
            reason=reason,
            next_page=next_page,
            next_path=next_path,
        )


def _operation_reason(args: Mapping[str, JsonValue], fallback: str) -> str:
    justification: JsonValue = args.get("justification")
    if isinstance(justification, dict):
        reason: JsonValue = justification.get("reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()
    return fallback


__all__: list[str] = ["OperationNoticeMiddleware"]
