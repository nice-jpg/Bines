"""Require structured, audited justification for policy-marked tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import NotRequired, TypedDict, cast

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from src.agent_messages.text import message_content
from src.agent_messages.tool_results import ToolResult
from src.common.event_log import WorkspaceEventLogger
from src.common.utils.json import json_object
from src.conductor.state import ConductorState, current_visit
from src.strategy.tool_io import ToolInvocation, tool_messages
from src.tools.shared.safety import (
    DangerousToolPolicy,
    dangerous_policy_from_metadata,
)


class ToolAuditResult(TypedDict):
    status: str
    content: str


class ToolAuditError(TypedDict):
    error_type: str
    error: str


class PolicyAudit(TypedDict):
    policy_id: str
    risk_level: str


class JustificationAudit(TypedDict):
    reason: str
    evidence: str
    expected_outcome: str
    risk_acknowledged: bool


class DangerousToolAudit(TypedDict):
    decision: str
    policy: PolicyAudit
    tool_name: str
    page_path: str
    page_visit_id: str
    justification: NotRequired[JustificationAudit]
    result: NotRequired[ToolAuditResult | ToolAuditError]


class DangerousToolMiddleware(AgentMiddleware):
    """Guard tools carrying a typed dangerous-action policy."""

    def __init__(self) -> None:
        self.logger: WorkspaceEventLogger = WorkspaceEventLogger()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        policy: DangerousToolPolicy | None = _request_policy(request)
        if policy is None:
            return handler(request)

        justification: JustificationAudit = cast(
            JustificationAudit, request.tool_call["args"]["justification"]
        )
        self._log(request, policy, "approved", justification=justification)
        try:
            result: ToolResult = handler(request)
        except Exception as error:
            self._log(
                request,
                policy,
                "execution_failed",
                justification=justification,
                result={"error_type": type(error).__name__, "error": str(error)},
            )
            raise
        self._log(
            request,
            policy,
            _execution_decision(result),
            justification=justification,
            result=_result_summary(result),
        )
        return result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        policy: DangerousToolPolicy | None = _request_policy(request)
        if policy is None:
            return await handler(request)

        justification: JustificationAudit = cast(
            JustificationAudit, request.tool_call["args"]["justification"]
        )
        self._log(request, policy, "approved", justification=justification)
        try:
            result: ToolResult = await handler(request)
        except Exception as error:
            self._log(
                request,
                policy,
                "execution_failed",
                justification=justification,
                result={"error_type": type(error).__name__, "error": str(error)},
            )
            raise
        self._log(
            request,
            policy,
            _execution_decision(result),
            justification=justification,
            result=_result_summary(result),
        )
        return result

    def _log(
        self,
        request: ToolCallRequest,
        policy: DangerousToolPolicy,
        decision: str,
        *,
        justification: JustificationAudit | None = None,
        result: ToolAuditResult | ToolAuditError | None = None,
    ) -> None:
        invocation: ToolInvocation = ToolInvocation.from_request(request)
        state: ConductorState = invocation.state
        details: DangerousToolAudit = {
            "decision": decision,
            "policy": {
                "policy_id": policy.policy_id,
                "risk_level": policy.risk_level.value,
            },
            "tool_name": invocation.name,
            "page_path": str(current_visit(state).get("path") or ""),
            "page_visit_id": str(current_visit(state).get("visit_id") or ""),
        }
        if justification is not None:
            details["justification"] = justification
        if result is not None:
            details["result"] = result
        self.logger.log("dangerous_tool_call", f"Dangerous tool call {decision}.", json_object(details))


def _request_policy(request: ToolCallRequest) -> DangerousToolPolicy | None:
    return dangerous_policy_from_metadata(ToolInvocation.from_request(request).tool_metadata)


def _result_summary(result: ToolResult) -> ToolAuditResult:
    typed_result: ToolResult = result
    messages: list[ToolMessage] = tool_messages(typed_result)
    if messages:
        message: ToolMessage = messages[-1]
        return {
            "status": message.status,
            "content": str(message_content(message)),
        }
    return {
        "status": "success",
        "content": str(result),
    }


def _execution_decision(result: ToolResult) -> str:
    messages: list[ToolMessage] = tool_messages(result)
    return "execution_failed" if messages and messages[-1].status == "error" else "executed"
