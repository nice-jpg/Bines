"""Require structured, audited justification for policy-marked tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
import json
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

if __package__ and __package__.startswith("src."):
    from src.middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from src.middleware.tool_io import ToolInvocation, require_tool_result, tool_messages
    from src.tools.event_logger import WorkspaceEventLogger
    from src.tools.safety import (
        DangerousActionJustification,
        DangerousToolPolicy,
        dangerous_policy_from_metadata,
    )
else:  # Supports running tests and python src/run_agent.py with src on sys.path.
    from middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from middleware.tool_io import ToolInvocation, require_tool_result, tool_messages
    from tools.event_logger import WorkspaceEventLogger
    from tools.safety import (
        DangerousActionJustification,
        DangerousToolPolicy,
        dangerous_policy_from_metadata,
    )


class DangerousToolMiddleware(AgentMiddleware):
    """Guard tools carrying a typed dangerous-action policy."""

    def __init__(self) -> None:
        self.logger = WorkspaceEventLogger()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Any],
    ) -> ToolMessage | Any:
        policy = _request_policy(request)
        if policy is None:
            return handler(request)

        justification, invalid_fields = _validated_justification(request)
        if justification is None:
            self._log(request, policy, "rejected", invalid_fields=invalid_fields)
            return _invalid_reason_message(request, invalid_fields)

        self._log(request, policy, "approved", justification=justification)
        try:
            result = handler(request)
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
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        policy = _request_policy(request)
        if policy is None:
            return await handler(request)

        justification, invalid_fields = _validated_justification(request)
        if justification is None:
            self._log(request, policy, "rejected", invalid_fields=invalid_fields)
            return _invalid_reason_message(request, invalid_fields)

        self._log(request, policy, "approved", justification=justification)
        try:
            result = await handler(request)
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
        result: dict[str, Any] | None = None,
    ) -> None:
        invocation = ToolInvocation.from_request(request)
        state = invocation.state
        details: dict[str, Any] = {
            "decision": decision,
            "policy": {
                **asdict(policy),
                "risk_level": policy.risk_level.value,
            },
            "tool_name": invocation.name,
            "page_path": str(state.get(PAGE_CONTEXT_PATH) or ""),
            "page_visit_id": str(state.get(PAGE_VISIT_ID) or ""),
        }
        if justification is not None:
            details["justification"] = justification.model_dump()
        if invalid_fields is not None:
            details["invalid_fields"] = invalid_fields
        if result is not None:
            details["result"] = result
        self.logger.log("dangerous_tool_call", f"Dangerous tool call {decision}.", details)


def _request_policy(request: ToolCallRequest) -> DangerousToolPolicy | None:
    return dangerous_policy_from_metadata(ToolInvocation.from_request(request).tool_metadata)


def _validated_justification(
    request: ToolCallRequest,
) -> tuple[DangerousActionJustification | None, list[str]]:
    raw_justification = ToolInvocation.from_request(request).args.get("justification")
    try:
        return DangerousActionJustification.model_validate(raw_justification), []
    except ValidationError as error:
        fields = sorted(
            {
                ".".join(str(part) for part in detail.get("loc", ())) or "justification"
                for detail in error.errors()
            }
        )
        return None, fields or ["justification"]


def _invalid_reason_message(request: ToolCallRequest, invalid_fields: list[str]) -> ToolMessage:
    invocation = ToolInvocation.from_request(request)
    tool_name = invocation.name or "unknown_tool"
    tool_call_id = invocation.call_id
    payload = {
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


def _result_summary(result: Any) -> dict[str, Any]:
    typed_result = require_tool_result(result)
    messages = tool_messages(typed_result)
    if messages:
        message = messages[-1]
        return {
            "status": message.status,
            "content": str(message.content),
        }
    return {
        "status": "success",
        "content": str(result),
    }

def _execution_decision(result: Any) -> str:
    messages = tool_messages(require_tool_result(result))
    return "execution_failed" if messages and messages[-1].status == "error" else "executed"
