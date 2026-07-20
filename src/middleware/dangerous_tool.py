"""Require structured, audited justification for policy-marked tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict
import json
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from pydantic import ValidationError

try:
    from src.middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from src.tools.event_logger import WorkspaceEventLogger
    from src.tools.safety import (
        DangerousActionJustification,
        DangerousToolPolicy,
        dangerous_policy_from_metadata,
    )
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
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
        request: Any,
        handler: Callable[[Any], ToolMessage | Any],
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
            "executed" if getattr(result, "status", None) != "error" else "execution_failed",
            justification=justification,
            result=_result_summary(result),
        )
        return result

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Any]],
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
            "executed" if getattr(result, "status", None) != "error" else "execution_failed",
            justification=justification,
            result=_result_summary(result),
        )
        return result

    def _log(
        self,
        request: Any,
        policy: DangerousToolPolicy,
        decision: str,
        *,
        justification: DangerousActionJustification | None = None,
        invalid_fields: list[str] | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        call = _tool_call(request)
        state = _state(request)
        details: dict[str, Any] = {
            "decision": decision,
            "policy": {
                **asdict(policy),
                "risk_level": policy.risk_level.value,
            },
            "tool_name": str(call.get("name") or getattr(getattr(request, "tool", None), "name", "")),
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


def _request_policy(request: Any) -> DangerousToolPolicy | None:
    return dangerous_policy_from_metadata(getattr(getattr(request, "tool", None), "metadata", None))


def _validated_justification(
    request: Any,
) -> tuple[DangerousActionJustification | None, list[str]]:
    raw = _tool_call(request).get("args", {})
    raw_justification = raw.get("justification") if isinstance(raw, Mapping) else None
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


def _invalid_reason_message(request: Any, invalid_fields: list[str]) -> ToolMessage:
    call = _tool_call(request)
    tool = getattr(request, "tool", None)
    tool_name = str(call.get("name") or getattr(tool, "name", None) or "unknown_tool")
    tool_call_id = str(call.get("id") or call.get("tool_call_id") or "")
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
    if isinstance(result, ToolMessage):
        return {
            "status": result.status,
            "content": str(result.content),
        }
    return {
        "status": "success",
        "content": str(result),
    }


def _tool_call(request: Any) -> Mapping[str, Any]:
    call = getattr(request, "tool_call", None)
    return call if isinstance(call, Mapping) else {}


def _state(request: Any) -> Mapping[str, Any]:
    state = getattr(request, "state", None)
    return state if isinstance(state, Mapping) else {}
