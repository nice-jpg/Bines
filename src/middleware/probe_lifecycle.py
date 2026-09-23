"""Deterministic termination gate for application-probe agent runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
import json
from typing import Any

from langchain.agents.middleware import hook_config
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command, interrupt

try:
    from src.middleware.page_context import (
        PAGE_CONTEXT_OVERLAY,
        PAGE_CONTEXT_PATH,
        PAGE_CONTEXT_PENDING_PATH,
        PAGE_VISIT_ID,
    )
    from src.middleware.tool_io import (
        ToolInvocation,
        command_state_value,
        require_tool_result,
        tool_succeeded,
        with_state_update,
    )
    from src.middleware.probe_state import (
        MERCHANT_COLLECTION_DONE,
        PROBE_COMPLETED,
        PROBE_COMPLETION_AUTHORIZED,
        PROBE_CONTINUATION_REASON,
        PROBE_PREMATURE_FINAL_COUNT,
        PROBE_RUNNING,
        PROBE_STATUS,
        PROBE_TARGET_LEAF_PATHS,
        PROBE_VISITED_LEAF_PATHS,
        ProbeState,
    )
    from src.page_mechanism import PageMechanism
    from src.tools.event_logger import WorkspaceEventLogger
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.page_context import (
        PAGE_CONTEXT_OVERLAY,
        PAGE_CONTEXT_PATH,
        PAGE_CONTEXT_PENDING_PATH,
        PAGE_VISIT_ID,
    )
    from middleware.tool_io import (
        ToolInvocation,
        command_state_value,
        require_tool_result,
        tool_succeeded,
        with_state_update,
    )
    from middleware.probe_state import (
        MERCHANT_COLLECTION_DONE,
        PROBE_COMPLETED,
        PROBE_COMPLETION_AUTHORIZED,
        PROBE_CONTINUATION_REASON,
        PROBE_PREMATURE_FINAL_COUNT,
        PROBE_RUNNING,
        PROBE_STATUS,
        PROBE_TARGET_LEAF_PATHS,
        PROBE_VISITED_LEAF_PATHS,
        ProbeState,
    )
    from page_mechanism import PageMechanism
    from tools.event_logger import WorkspaceEventLogger


MAX_PREMATURE_FINALS = 3


class ProbeLifecycleMiddleware(AgentMiddleware[ProbeState]):
    """Own probe completion, premature-final recovery, and liveness interrupts."""

    state_schema = ProbeState

    def __init__(self, *, mechanism: PageMechanism | None = None) -> None:
        self.mechanism = mechanism or PageMechanism()
        self._logger = WorkspaceEventLogger()

    def create_tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                func=self.authorize_probe_completion,
                name="authorize_probe_completion",
                description=(
                    "Request deterministic authorization to finish the active application probe. "
                    "Call only after all configured leaf pages have been visited, no navigation or "
                    "overlay is pending, and the conductor has returned from every merchant page. "
                    "A successful authorization must be followed by close_package."
                ),
            )
        ]

    def authorize_probe_completion(self, runtime: ToolRuntime) -> Command:
        """Authorize completion only when all deterministic invariants hold."""

        state = runtime.state if isinstance(runtime.state, Mapping) else {}
        result, update = self._completion_evaluation(state)
        message = ToolMessage(
            content=json.dumps(result, ensure_ascii=False, sort_keys=True),
            tool_call_id=str(runtime.tool_call_id or ""),
            name="authorize_probe_completion",
            status="success" if result["ok"] else "error",
            additional_kwargs={} if result["ok"] else {"error_type": result["error_type"]},
        )
        if result["ok"]:
            self._logger.log(
                "completion_authorized",
                "Probe completion was authorized.",
                {
                    "page_path": state.get(PAGE_CONTEXT_PATH, ""),
                    "page_visit_id": state.get(PAGE_VISIT_ID, ""),
                    "visited_leaf_paths": list(state.get(PROBE_VISITED_LEAF_PATHS) or ()),
                },
            )
        return Command(update={"messages": [message], **update})

    @hook_config(can_jump_to=["model"])
    def after_model(self, state: ProbeState, runtime: Any) -> dict[str, Any] | None:
        return self._after_model(state)

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: ProbeState, runtime: Any) -> dict[str, Any] | None:
        return self._after_model(state)

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]) -> Any:
        rejection = self._validate_tool_call(request)
        if rejection is not None:
            return rejection
        return self._apply_tool_transition(request, handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        rejection = self._validate_tool_call(request)
        if rejection is not None:
            return rejection
        return self._apply_tool_transition(request, await handler(request))

    def _after_model(self, state: Mapping[str, Any]) -> dict[str, Any] | None:
        status = str(state.get(PROBE_STATUS) or "")
        latest = _latest_ai_message(state.get("messages") or ())
        if latest is None:
            return None
        gated_statuses = {PROBE_RUNNING, PROBE_COMPLETION_AUTHORIZED}
        if _message_tool_calls(latest):
            update: dict[str, Any] = {}
            continuation_ids = _continuation_message_ids(state.get("messages") or ())
            if continuation_ids:
                update["messages"] = [RemoveMessage(id=message_id) for message_id in continuation_ids]
            if status in gated_statuses and int(state.get(PROBE_PREMATURE_FINAL_COUNT, 0)):
                update[PROBE_PREMATURE_FINAL_COUNT] = 0
            if update:
                return update
            return None
        if status not in gated_statuses:
            return None

        count = int(state.get(PROBE_PREMATURE_FINAL_COUNT, 0)) + 1
        reason = self._continuation_reason(state)
        details = {
            "page_path": str(state.get(PAGE_CONTEXT_PATH) or ""),
            "page_visit_id": str(state.get(PAGE_VISIT_ID) or ""),
            "attempt": count,
            "reason": reason,
        }
        self._logger.log(
            "premature_termination_rejected",
            "A probe response without tool calls was rejected.",
            details,
        )
        if count >= MAX_PREMATURE_FINALS:
            payload = {
                "kind": "liveness",
                "message": "Agent连续三次未选择可执行工具。回复“继续”可从当前任务状态恢复。",
                "page_path": details["page_path"],
                "page_visit_id": details["page_visit_id"],
                "attempts": count,
                "reason": reason,
                "permitted_tools": self._permitted_tools(state),
            }
            self._logger.log("liveness_interrupted", "Probe liveness interrupt created.", payload)
            interrupt(payload)
            count = 0

        correction = SystemMessage(
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
        message_updates: list[Any] = []
        if latest.id:
            message_updates.append(RemoveMessage(id=latest.id))
        message_updates.append(correction)
        return {
            "messages": message_updates,
            PROBE_PREMATURE_FINAL_COUNT: count,
            PROBE_CONTINUATION_REASON: reason,
            "jump_to": "model",
        }

    def _validate_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        invocation = ToolInvocation.from_request(request)
        state = invocation.state
        if str(state.get(PROBE_STATUS) or "") not in {PROBE_RUNNING, PROBE_COMPLETION_AUTHORIZED}:
            return None
        tool_name = invocation.name
        if tool_name == "close_package" and state.get(PROBE_STATUS) != PROBE_COMPLETION_AUTHORIZED:
            return _lifecycle_error(
                request,
                "probe_completion_not_authorized",
                "Call authorize_probe_completion successfully before close_package.",
            )
        if tool_name == "navigate_back":
            current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
            if self.mechanism.is_merchant_page(current_path) and state.get(MERCHANT_COLLECTION_DONE) is not True:
                return _lifecycle_error(
                    request,
                    "merchant_collection_incomplete",
                    "A merchant page can be left only after is_done returns done=true.",
                )
        return None

    def _apply_tool_transition(self, request: ToolCallRequest, result: Any) -> Any:
        invocation = ToolInvocation.from_request(request)
        state = invocation.state
        typed_result = require_tool_result(result)
        if not tool_succeeded(typed_result):
            return result
        tool_name = invocation.name
        update: dict[str, Any] = {}
        transitioned_path = command_state_value(typed_result, PAGE_CONTEXT_PATH)
        if tool_name == "close_package" and state.get(PROBE_STATUS) == PROBE_COMPLETION_AUTHORIZED:
            update.update(
                {
                    PROBE_STATUS: PROBE_COMPLETED,
                    PROBE_PREMATURE_FINAL_COUNT: 0,
                    PROBE_CONTINUATION_REASON: "",
                }
            )
            self._logger.log(
                "probe_completed",
                "The authorized probe closed its application and completed.",
                {
                    "page_path": state.get(PAGE_CONTEXT_PATH, ""),
                    "page_visit_id": state.get(PAGE_VISIT_ID, ""),
                },
            )
        elif transitioned_path is not None:
            path = str(transitioned_path)
            targets = tuple(str(item) for item in state.get(PROBE_TARGET_LEAF_PATHS) or ())
            visited = list(str(item) for item in state.get(PROBE_VISITED_LEAF_PATHS) or ())
            if path in targets and path not in visited:
                visited.append(path)
                update[PROBE_VISITED_LEAF_PATHS] = tuple(visited)
            update[PROBE_PREMATURE_FINAL_COUNT] = 0
        elif state.get(PROBE_STATUS) == PROBE_RUNNING:
            update[PROBE_PREMATURE_FINAL_COUNT] = 0
        return with_state_update(typed_result, update) if update else result

    def _completion_evaluation(self, state: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        missing: list[str] = []
        if state.get(PROBE_STATUS) != PROBE_RUNNING:
            missing.append("probe_not_running")
        targets = tuple(str(item) for item in state.get(PROBE_TARGET_LEAF_PATHS) or ())
        visited = set(str(item) for item in state.get(PROBE_VISITED_LEAF_PATHS) or ())
        missing.extend(f"unvisited_leaf:{path}" for path in targets if path not in visited)
        if state.get(PAGE_CONTEXT_OVERLAY):
            missing.append("overlay_active")
        if state.get(PAGE_CONTEXT_PENDING_PATH):
            missing.append("navigation_pending")
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        if not current_path:
            missing.append("application_not_open")
        elif self.mechanism.is_merchant_page(current_path):
            missing.append("merchant_page_active")
            if state.get(MERCHANT_COLLECTION_DONE) is not True:
                missing.append("merchant_collection_incomplete")

        if missing:
            return (
                {
                    "ok": False,
                    "authorized": False,
                    "error_type": "probe_completion_requirements_missing",
                    "missing": missing,
                },
                {
                    PROBE_STATUS: PROBE_RUNNING,
                    PROBE_CONTINUATION_REASON: missing[0],
                },
            )
        return (
            {"ok": True, "authorized": True, "missing": []},
            {
                PROBE_STATUS: PROBE_COMPLETION_AUTHORIZED,
                PROBE_CONTINUATION_REASON: "",
                PROBE_PREMATURE_FINAL_COUNT: 0,
            },
        )

    def _continuation_reason(self, state: Mapping[str, Any]) -> str:
        if state.get(PROBE_STATUS) == PROBE_COMPLETION_AUTHORIZED:
            return "application_close_required"
        if state.get(PAGE_CONTEXT_OVERLAY):
            return "overlay_active"
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        if current_path and self.mechanism.is_merchant_page(current_path):
            if state.get(MERCHANT_COLLECTION_DONE) is not True:
                return "merchant_collection_incomplete"
        targets = set(str(item) for item in state.get(PROBE_TARGET_LEAF_PATHS) or ())
        visited = set(str(item) for item in state.get(PROBE_VISITED_LEAF_PATHS) or ())
        if targets - visited:
            return "configured_leaf_pages_unvisited"
        return "completion_not_authorized"

    def _permitted_tools(self, state: Mapping[str, Any]) -> list[str]:
        if state.get(PAGE_CONTEXT_OVERLAY):
            return ["tap_in_page"]
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        if not current_path:
            return ["run_package"]
        if self.mechanism.is_merchant_page(current_path):
            tools = [
                "prepare_collector",
                "page_view",
                "swipe_up",
                "swipe_up_long",
                "swipe_down",
                "is_done",
            ]
            if state.get(MERCHANT_COLLECTION_DONE) is True:
                tools.append("navigate_back")
            return tools
        return ["page_view", "tap_in_page", "navigate_to", "navigate_back", "authorize_probe_completion"]


def _latest_ai_message(messages: Sequence[Any]) -> AIMessage | None:
    for message in reversed(messages):
        if isinstance(message, AIMessage):
            return message
        if isinstance(message, Mapping) and message.get("role") == "assistant":
            return AIMessage(
                content=message.get("content", ""),
                id=message.get("id"),
                tool_calls=message.get("tool_calls") or [],
            )
    return None


def _message_tool_calls(message: AIMessage) -> Sequence[Any]:
    return message.tool_calls


def _continuation_message_ids(messages: Sequence[Any]) -> list[str]:
    ids: list[str] = []
    for message in messages:
        if not isinstance(message, SystemMessage):
            continue
        context = message.additional_kwargs.get("bines_context", {})
        if isinstance(context, Mapping) and context.get("kind") == "probe_runtime_continuation":
            message_id = message.id
            if message_id:
                ids.append(str(message_id))
    return ids


def _lifecycle_error(request: Any, error_type: str, detail: str) -> ToolMessage:
    invocation = ToolInvocation.from_request(request)
    return ToolMessage(
        content=json.dumps(
            {
                "ok": False,
                "error_type": error_type,
                "detail": detail,
                "page_path": str(invocation.state.get(PAGE_CONTEXT_PATH) or ""),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        tool_call_id=invocation.call_id,
        name=invocation.name or "unknown_tool",
        status="error",
        additional_kwargs={"error_type": error_type},
    )


__all__ = ["MAX_PREMATURE_FINALS", "ProbeLifecycleMiddleware"]
