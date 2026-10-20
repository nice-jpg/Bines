"""Require structured, audited justification for policy-marked tools."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import NotRequired, TypedDict

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

from src.agent_messages.text import message_content
from src.agent_messages.tool_results import ToolResult
from src.common.event_log import WorkspaceEventLogger
from src.common.utils.json import JsonValue, json_object
from src.conductor.state import ConductorState, current_visit
from src.middleware.tool_io import ToolInvocation, tool_messages
from src.tools.shared.safety import (
    DangerousActionJustification,
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
    invalid_fields: NotRequired[list[str]]
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

        justification, invalid_fields = _validated_justification(request)
        if justification is None:
            self._log(request, policy, "rejected", invalid_fields=invalid_fields)
            return _invalid_reason_message(request, invalid_fields)

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

        justification, invalid_fields = _validated_justification(request)
        if justification is None:
            self._log(request, policy, "rejected", invalid_fields=invalid_fields)
            return _invalid_reason_message(request, invalid_fields)

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
        justification: DangerousActionJustification | None = None,
        invalid_fields: list[str] | None = None,
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
            details["justification"] = {
                "reason": justification.reason,
                "evidence": justification.evidence,
                "expected_outcome": justification.expected_outcome,
                "risk_acknowledged": justification.risk_acknowledged,
            }
        if invalid_fields is not None:
            details["invalid_fields"] = invalid_fields
        if result is not None:
            details["result"] = result
        self.logger.log("dangerous_tool_call", f"Dangerous tool call {decision}.", json_object(details))


def _request_policy(request: ToolCallRequest) -> DangerousToolPolicy | None:
    return dangerous_policy_from_metadata(ToolInvocation.from_request(request).tool_metadata)


def _validated_justification(
    request: ToolCallRequest,
) -> tuple[DangerousActionJustification | None, list[str]]:
    raw_justification: JsonValue = ToolInvocation.from_request(request).args.get("justification")
    try:
        return DangerousActionJustification.model_validate(raw_justification), []
    except ValidationError as error:
        fields: list[str] = sorted(
            {
                ".".join(str(part) for part in detail.get("loc", ())) or "justification"
                for detail in error.errors()
            }
        )
        return None, fields or ["justification"]


def _invalid_reason_message(request: ToolCallRequest, invalid_fields: list[str]) -> ToolMessage:
    invocation: ToolInvocation = ToolInvocation.from_request(request)
    tool_name: str = invocation.name or "unknown_tool"
    tool_call_id: str = invocation.call_id
    payload: dict[str, bool | str | list[str]] = {
        "ok": False,
        "error_type": "dangerous_action_reason_invalid",
        "tool_name": tool_name,
        "invalid_fields": invalid_fields,
    }
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        tool_call_id=tool_call_id,
        name=tool_name,
        status="error",
        additional_kwargs={
            "error_type": "dangerous_action_reason_invalid",
            "invalid_fields": invalid_fields,
        },
    )


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
