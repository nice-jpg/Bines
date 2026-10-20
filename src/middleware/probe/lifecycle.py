"""Deterministic termination gate for application-probe agent runs."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Final, Literal

from langchain.agents.middleware import hook_config
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, AnyMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from src.agent_messages.text import message_additional_kwargs
from src.agent_messages.tool_results import ToolResult
from src.common.event_log import WorkspaceEventLogger
from src.common.utils.validation import is_string_mapping
from src.conductor.state import (
    PROBE_COMPLETED,
    PROBE_COMPLETION_AUTHORIZED,
    PROBE_RUNNING,
    REASON_APPLICATION_CLOSE,
    REASON_APPLICATION_NOT_OPEN,
    REASON_MERCHANT_ACTIVE,
    REASON_MERCHANT_INCOMPLETE,
    REASON_NOT_AUTHORIZED,
    REASON_NOT_RUNNING,
    REASON_PAGE_CONFIRMATION,
    REASON_UNFINISHED_MERCHANTS,
    REASON_UNVISITED_PAGES,
    UNVISITED_LEAF_PREFIX,
    ConductorFields,
    ConductorState,
    ConductorUpdate,
    collection_completed,
    combine_updates,
    complete_update,
    current_visit,
    merge_fields,
    read_conductor_fields,
    read_conductor_state,
)
from src.middleware.tool_io import (
    ToolInvocation,
    command_update,
    tool_succeeded,
    with_state_update,
)
from src.page.mechanism import PageMechanism
from src.strategy.general.declarations import requires_collection
from src.strategy.general.state import PageVisit
from src.strategy.merchant.state import MERCHANT_COMPLETED, is_merchant_visit
from src.tools.shared.factory import create_tool

MAX_PREMATURE_FINALS: Final[int] = 3


class ProbeCompletionAccepted(TypedDict):
    ok: Literal[True]
    authorized: Literal[True]
    missing: list[str]


class ProbeCompletionRejected(TypedDict):
    ok: Literal[False]
    authorized: Literal[False]
    error_type: str
    missing: list[str]


type ProbeCompletionResult = ProbeCompletionAccepted | ProbeCompletionRejected


class ProbeLifecycleMiddleware(AgentMiddleware[ConductorState, None]):
    """Own probe completion, premature-final recovery, and liveness interrupts."""

    state_schema: type[ConductorState] = ConductorState

    def __init__(
        self,
        *,
        mechanism: PageMechanism | None = None,
    ) -> None:
        self.mechanism: PageMechanism = mechanism or PageMechanism()
        self._logger: WorkspaceEventLogger = WorkspaceEventLogger()

    def create_tools(self) -> list[StructuredTool]:
        return [
            create_tool(
                func=self.authorize_probe_completion,
                name="authorize_probe_completion",
                description=(
                    "Request deterministic authorization to finish the active application probe. "
                    "Call only after all configured leaf pages have been visited and the conductor "
                    "has returned from every merchant page. "
                    "A successful authorization must be followed by close_package."
                ),
            )
        ]

    def authorize_probe_completion(self, runtime: ToolRuntime[None, ConductorState]) -> Command[str]:
        """Authorize completion only when all deterministic invariants hold."""

        state: ConductorState = runtime.state
        result, update = self._completion_evaluation(state)
        message: ToolMessage = ToolMessage(
            content=json.dumps(result, ensure_ascii=False, sort_keys=True),
            tool_call_id=str(runtime.tool_call_id or ""),
            name="authorize_probe_completion",
            status="success" if result["ok"] is True else "error",
            additional_kwargs={} if result["ok"] is True else {"error_type": result["error_type"]},
        )
        if result["ok"] is True:
            self._logger.log(
                "completion_authorized",
                "Probe completion was authorized.",
                {
                    "page_path": current_visit(state).get("path", ""),
                    "page_visit_id": current_visit(state).get("visit_id", ""),
                    "visited_leaf_paths": list(state["probe"].get("visited_leaf_paths") or ()),
                },
            )
        return Command(update={"messages": [message], **complete_update(state, update)})

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: ConductorState, runtime: Runtime[None]) -> dict[str, object] | None:
        update: ConductorUpdate | None = self._after_model(read_conductor_state(state))
        return dict(complete_update(read_conductor_fields(state), update)) if update is not None else None

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: ConductorState, runtime: Runtime[None]) -> dict[str, object] | None:
        update: ConductorUpdate | None = self._after_model(read_conductor_state(state))
        return dict(complete_update(read_conductor_fields(state), update)) if update is not None else None

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        rejection: ToolMessage | None = self._validate_tool_call(request)
        if rejection is not None:
            return self._apply_tool_transition(request, rejection)
        return self._apply_tool_transition(request, handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        rejection: ToolMessage | None = self._validate_tool_call(request)
        if rejection is not None:
            return self._apply_tool_transition(request, rejection)
        return self._apply_tool_transition(request, await handler(request))

    def _after_model(self, state: ConductorState) -> ConductorUpdate | None:
        status: str = str(state["probe"].get("status") or "")
        latest: AIMessage | None = _latest_ai_message(state.get("messages") or ())
        if latest is None:
            return None
        gated_statuses: set[str] = {PROBE_RUNNING, PROBE_COMPLETION_AUTHORIZED}
        if status in gated_statuses and int(state["probe"].get("failed_tool_count", 0)) >= 3:
            interrupt(
                {
                    "kind": "liveness",
                    "reason": "repeated_tool_failures",
                    "message": "连续工具调用失败。请检查诊断后回复继续，或调整恢复方案。",
                    "page_path": current_visit(state).get("path"),
                    "page_visit_id": current_visit(state).get("visit_id"),
                }
            )
            return {"probe": {"failed_tool_count": 0}}
        if latest.tool_calls:
            update: ConductorUpdate = {}
            continuation_ids: list[str] = _continuation_message_ids(state.get("messages") or ())
            if continuation_ids:
                removals: list[AnyMessage | RemoveMessage] = [
                    RemoveMessage(id=message_id) for message_id in continuation_ids
                ]
                update["messages"] = removals
            if status in gated_statuses and int(state["probe"].get("premature_final_count", 0)):
                update.setdefault("probe", {})["premature_final_count"] = 0
            if update:
                return update
            return None
        if status not in gated_statuses:
            return None

        count: int = int(state["probe"].get("premature_final_count", 0)) + 1
        reason: str = self._continuation_reason(state)
        details: dict[str, str | int] = {
            "page_path": str(current_visit(state).get("path") or ""),
            "page_visit_id": str(current_visit(state).get("visit_id") or ""),
            "attempt": count,
            "reason": reason,
        }
        self._logger.log(
            "premature_termination_rejected",
            "A probe response without tool calls was rejected.",
            details,
        )
        if count >= MAX_PREMATURE_FINALS:
            payload: dict[str, str | int] = {
                "kind": "liveness",
                "message": "Agent连续三次未选择可执行工具。回复“继续”可从当前任务状态恢复。",
                "page_path": details["page_path"],
                "page_visit_id": details["page_visit_id"],
                "attempts": count,
                "reason": reason,
                "guidance": "Inspect the latest tool diagnostics; recovery navigation requires recovery_reason.",
            }
            self._logger.log("liveness_interrupted", "Probe liveness interrupt created.", payload)
            interrupt(payload)
            count = 0

        correction: SystemMessage = SystemMessage(
            content=(
                "<probe_runtime_continuation>\n"
                f"reason: {reason}\n"
                "The probe is still running. Do not answer in natural language. "
                "Immediately select one currently available tool that advances the task.\n"
                "</probe_runtime_continuation>"
            ),
            id="probe-runtime-continuation",
            additional_kwargs={
                "bines_context": {
                    "kind": "probe_runtime_continuation",
                    "reason": reason,
                }
            },
        )
        message_updates: list[AnyMessage | RemoveMessage] = []
        if latest.id:
            message_updates.append(RemoveMessage(id=latest.id))
        message_updates.append(correction)
        return combine_updates(
            {"probe": {"premature_final_count": count, "continuation_reason": reason}},
            {"messages": message_updates, "jump_to": "model"},
        )

    def _validate_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        invocation: ToolInvocation = ToolInvocation.from_request(request)
        state: ConductorState = invocation.state
        if str(state["probe"].get("status") or "") not in {PROBE_RUNNING, PROBE_COMPLETION_AUTHORIZED}:
            return None
        tool_name: str = invocation.name
        if tool_name == "close_package" and state["probe"].get("status") != PROBE_COMPLETION_AUTHORIZED:
            return _lifecycle_error(
                request,
                "probe_completion_not_authorized",
                "Call authorize_probe_completion successfully before close_package.",
            )
        return None

    def _apply_tool_transition(self, request: ToolCallRequest, result: ToolResult) -> ToolResult:
        invocation: ToolInvocation = ToolInvocation.from_request(request)
        state: ConductorState = invocation.state
        typed_result: ToolResult = result
        if not tool_succeeded(typed_result):
            return with_state_update(
                typed_result,
                complete_update(
                    state,
                    {"probe": {"failed_tool_count": int(state["probe"].get("failed_tool_count", 0)) + 1}},
                ),
            )
        tool_name: str = invocation.name
        update: ConductorUpdate = {"probe": {"failed_tool_count": 0}}
        unfinished: dict[str, dict[str, str]] = dict(state["probe"].get("unfinished_merchants") or {})
        current_path: str = str(current_visit(state).get("path") or "")
        merchant: str = str(state["probe"].get("current_merchant") or "")
        result_fields: ConductorFields = (
            merge_fields(state, command_update(typed_result))
            if isinstance(typed_result, Command)
            else read_conductor_fields(state)
        )
        result_visit: PageVisit | None = result_fields["general"].get("visit")
        if tool_name in {"is_done", "collect_continuously"} and (
            result_visit is not None
            and is_merchant_visit(result_visit)
            and result_fields["merchant"].get("phase") == MERCHANT_COMPLETED
        ):
            unfinished = {
                key: value
                for key, value in unfinished.items()
                if (value["path"], value["merchant"]) != (current_path, merchant)
            }
            update.setdefault("probe", {})["unfinished_merchants"] = unfinished
        transitioned_path: object = (
            current_visit(result_fields)["path"]
            if isinstance(typed_result, Command) and "general" in command_update(typed_result)
            else None
        )
        if tool_name == "close_package" and state["probe"].get("status") == PROBE_COMPLETION_AUTHORIZED:
            update.update(
                {"probe": {"status": PROBE_COMPLETED, "premature_final_count": 0, "continuation_reason": ""}}
            )
            self._logger.log(
                "probe_completed",
                "The authorized probe closed its application and completed.",
                {
                    "page_path": current_visit(state).get("path", ""),
                    "page_visit_id": current_visit(state).get("visit_id", ""),
                },
            )
        elif (
            isinstance(transitioned_path, str)
            and result_fields["general"].get("pending") is None
            and result_visit is not None
            and result_visit != state["general"].get("visit")
        ):
            path: str = transitioned_path
            update.setdefault("probe", {})["status"] = PROBE_RUNNING
            targets: tuple[str, ...] = state["probe"].get("target_leaf_paths", ())
            visited: list[str] = list(state["probe"].get("visited_leaf_paths", ()))
            if path in targets and path not in visited:
                visited.append(path)
                update.setdefault("probe", {})["visited_leaf_paths"] = tuple(visited)
            update.setdefault("probe", {})["premature_final_count"] = 0
        elif state["probe"].get("status") == PROBE_RUNNING:
            update.setdefault("probe", {})["premature_final_count"] = 0
        if state["probe"].get("status") == PROBE_COMPLETION_AUTHORIZED and tool_name not in {
            "close_package",
            "authorize_probe_completion",
        }:
            update.setdefault("probe", {})["status"] = PROBE_RUNNING
        return with_state_update(typed_result, complete_update(result_fields, update)) if update else result

    def _completion_evaluation(self, state: ConductorFields) -> tuple[ProbeCompletionResult, ConductorUpdate]:
        missing: list[str] = []
        if state["general"].get("pending") is not None:
            missing.append(REASON_PAGE_CONFIRMATION)
        if state["probe"].get("unfinished_merchants"):
            missing.append(REASON_UNFINISHED_MERCHANTS)
        if state["probe"].get("status") != PROBE_RUNNING:
            missing.append(REASON_NOT_RUNNING)
        targets: tuple[str, ...] = state["probe"].get("target_leaf_paths", ())
        visited: set[str] = set(state["probe"].get("visited_leaf_paths", ()))
        missing.extend(f"{UNVISITED_LEAF_PREFIX}{path}" for path in targets if path not in visited)
        current_path: str = str(current_visit(state).get("path") or "")
        if not current_path:
            missing.append(REASON_APPLICATION_NOT_OPEN)
        elif self._is_structured(state):
            missing.append(REASON_MERCHANT_ACTIVE)
            if not collection_completed(state):
                missing.append(REASON_MERCHANT_INCOMPLETE)

        if missing:
            return (
                {
                    "ok": False,
                    "authorized": False,
                    "error_type": "probe_completion_requirements_missing",
                    "missing": missing,
                },
                {"probe": {"status": PROBE_RUNNING, "continuation_reason": missing[0]}},
            )
        return (
            {"ok": True, "authorized": True, "missing": []},
            {
                "probe": {
                    "status": PROBE_COMPLETION_AUTHORIZED,
                    "continuation_reason": "",
                    "premature_final_count": 0,
                }
            },
        )

    def _is_structured(self, state: ConductorFields) -> bool:
        visit: PageVisit | None = state["general"].get("visit")
        return (
            is_merchant_visit(visit)
            if visit is not None
            else requires_collection(self.mechanism, current_visit(state).get("path", ""))
        )

    def _continuation_reason(self, state: ConductorFields) -> str:
        if state["general"].get("pending") is not None:
            return REASON_PAGE_CONFIRMATION
        if state["probe"].get("status") == PROBE_COMPLETION_AUTHORIZED:
            return REASON_APPLICATION_CLOSE
        current_path: str = str(current_visit(state).get("path") or "")
        if current_path and self._is_structured(state):
            if not collection_completed(state):
                return REASON_MERCHANT_INCOMPLETE
        targets: set[str] = set(state["probe"].get("target_leaf_paths", ()))
        visited: set[str] = set(state["probe"].get("visited_leaf_paths", ()))
        if targets - visited:
            return REASON_UNVISITED_PAGES
        return REASON_NOT_AUTHORIZED


def _latest_ai_message(messages: Sequence[AnyMessage]) -> AIMessage | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message
    return None


def _continuation_message_ids(messages: Sequence[AnyMessage]) -> list[str]:
    ids: list[str] = []
    for message in messages:
        if not isinstance(message, SystemMessage):
            continue
        context: object = message_additional_kwargs(message).get("bines_context")
        if is_string_mapping(context) and context.get("kind") == "probe_runtime_continuation":
            message_id: str | None = message.id
            if message_id:
                ids.append(str(message_id))
    return ids


def _lifecycle_error(request: ToolCallRequest, error_type: str, detail: str) -> ToolMessage:
    invocation: ToolInvocation = ToolInvocation.from_request(request)
    return ToolMessage(
        content=json.dumps(
            {
                "ok": False,
                "error_type": error_type,
                "detail": detail,
                "page_path": str(current_visit(invocation.state).get("path") or ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        tool_call_id=invocation.call_id,
        name=invocation.name or "unknown_tool",
        status="error",
        additional_kwargs={"error_type": error_type},
    )


__all__: list[str] = ["MAX_PREMATURE_FINALS", "ProbeLifecycleMiddleware"]
