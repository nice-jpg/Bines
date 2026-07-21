"""Thread-scoped page context and explicit navigation transitions."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
import hashlib
import json
import re
from threading import Lock
from typing import Annotated, Any
from uuid import uuid4

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage
from langgraph.types import Command
from typing_extensions import NotRequired

try:
    from src.middleware.probe_state import reset_collection_state
    from src.page_mechanism import PageMechanism, normalize_page_path
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.probe_state import reset_collection_state
    from page_mechanism import PageMechanism, normalize_page_path


PAGE_CONTEXT_ACTIVE = "page_context_active"
PAGE_CONTEXT_APP_ROOT = "page_context_app_root"
PAGE_CONTEXT_PATH = "page_context_path"
PAGE_CONTEXT_PENDING_PATH = "page_context_pending_path"
PAGE_CONTEXT_OVERLAY = "page_context_overlay"
PAGE_VISIT_ID = "page_visit_id"
PAGE_PRODUCT_ENTRY_TAPS = "page_product_entry_taps"
PAGE_COLLECTION_STARTED = "page_collection_started"


def _replace_state_value(_current: Any, new: Any) -> Any:
    return new


class PageContextState(AgentState):
    """Page identity persisted independently for each LangGraph thread."""

    page_context_active: NotRequired[Annotated[bool, _replace_state_value]]
    page_context_app_root: NotRequired[Annotated[str, _replace_state_value]]
    page_context_path: NotRequired[Annotated[str, _replace_state_value]]
    page_context_pending_path: NotRequired[Annotated[str, _replace_state_value]]
    page_context_overlay: NotRequired[Annotated[bool, _replace_state_value]]
    page_visit_id: NotRequired[Annotated[str, _replace_state_value]]
    page_product_entry_taps: NotRequired[Annotated[int, _replace_state_value]]
    page_collection_started: NotRequired[Annotated[bool, _replace_state_value]]


DEVICE_ACTION_TOOLS = frozenset(
    {
        "run_package",
        "close_package",
        "tap_in_page",
        "navigate_to",
        "navigate_back",
        "swipe_up",
        "swipe_down",
        "uiautomate",
        "screenshot",
    }
)
OVERLAY_VISIBLE_TOOLS = frozenset(
    {
        "tap_in_page",
        "notify_user",
        "think",
        "authenticate_captcha",
        "captcha_authenticated",
        "close_package",
    }
)
PRE_LAUNCH_VISIBLE_TOOLS = frozenset(
    {
        "run_package",
        "close_package",
        "notify_user",
        "think",
        "authenticate_captcha",
        "captcha_authenticated",
    }
)
BOUNDS_RE = re.compile(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]')
DEFAULT_VIEWPORT_WIDTH = 1080
DEFAULT_VIEWPORT_HEIGHT = 2400


class PageContextMiddleware(AgentMiddleware[PageContextState]):
    """Apply page-scoped tool visibility and explicit navigation transitions."""

    state_schema = PageContextState

    def __init__(self, *, mechanism: PageMechanism | None = None) -> None:
        self.mechanism = mechanism or PageMechanism()
        self._action_claims: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._action_claim_lock = Lock()

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._prepare_model_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._prepare_model_request(request))

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]) -> Any:
        rejection = self._validate_tool_call(request)
        if rejection is not None:
            return rejection
        rejection = self._claim_device_action(request)
        if rejection is not None:
            return rejection
        return self._apply_transition(request, handler(request))

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        rejection = self._validate_tool_call(request)
        if rejection is not None:
            return rejection
        rejection = self._claim_device_action(request)
        if rejection is not None:
            return rejection
        return self._apply_transition(request, await handler(request))

    def _prepare_model_request(self, request: ModelRequest) -> ModelRequest:
        state = _state(request)
        if not state.get(PAGE_CONTEXT_ACTIVE, False):
            return request

        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        if state.get(PAGE_CONTEXT_OVERLAY, False):
            tools = _tools_named(request.tools, OVERLAY_VISIBLE_TOOLS)
        elif not current_path:
            tools = _tools_named(request.tools, PRE_LAUNCH_VISIBLE_TOOLS)
        else:
            tools = list(request.tools)
            policy = self.mechanism.page_policy(current_path)
            if policy.role == "merchant" and (
                state.get(PAGE_COLLECTION_STARTED, False)
                or int(state.get(PAGE_PRODUCT_ENTRY_TAPS, 0)) >= policy.max_product_entry_taps
            ):
                tools = [tool for tool in tools if _tool_name(tool) != "tap_in_page"]

        model_settings = dict(request.model_settings or {})
        model_settings["parallel_tool_calls"] = False
        return request.override(tools=tools, model_settings=model_settings)

    def _validate_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        state = _state(request)
        if not state.get(PAGE_CONTEXT_ACTIVE, False):
            return None
        tool_name = _request_tool_name(request)
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")

        if state.get(PAGE_CONTEXT_OVERLAY, False) and tool_name in DEVICE_ACTION_TOOLS - {"tap_in_page", "close_package"}:
            return _context_error(request, "overlay_requires_close", "Close the overlay with tap_in_page first.")
        if not current_path and tool_name in DEVICE_ACTION_TOOLS - {"run_package", "close_package"}:
            return _context_error(request, "application_not_open", "Call run_package before page operations.")

        args = _request_args(request)
        if tool_name == "run_package":
            try:
                self.mechanism.home_path_for_package(str(args.get("package_name") or ""))
            except (ValueError, FileNotFoundError) as exc:
                return _context_error(request, "invalid_app_home", str(exc))
        if tool_name in {"navigate_to", "navigate_back"}:
            error = self._navigation_error(state, str(args.get("next_path") or ""))
            if error:
                return _context_error(request, "invalid_navigation_target", error)
        if tool_name == "tap_in_page" and not state.get(PAGE_CONTEXT_OVERLAY, False):
            policy = self.mechanism.page_policy(current_path)
            taps_used = int(state.get(PAGE_PRODUCT_ENTRY_TAPS, 0))
            if policy.role == "merchant" and state.get(PAGE_COLLECTION_STARTED, False):
                return _context_error(
                    request,
                    "merchant_collection_already_started",
                    "Merchant category taps are forbidden after product scrolling starts.",
                )
            if policy.role == "merchant" and taps_used >= policy.max_product_entry_taps:
                return _context_error(
                    request,
                    "merchant_entry_tap_limit",
                    "The merchant product-entry tap allowance has already been used.",
                )
        return None

    def _navigation_error(self, state: Mapping[str, Any], next_path: str) -> str:
        parts, error = normalize_page_path(next_path)
        target = "/".join(parts)
        app_root = str(state.get(PAGE_CONTEXT_APP_ROOT) or "")
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        if error or not target:
            return error or "next_path is required."
        if not app_root or target.split("/", 1)[0] != app_root:
            return f"next_path must be absolute from app root {app_root!r}."
        if target == current_path:
            return "Use tap_in_page for same-page actions; navigation target must differ from current page."
        try:
            self.mechanism.validate_page_manual(target)
        except (ValueError, FileNotFoundError) as exc:
            return str(exc)
        return ""

    def _claim_device_action(self, request: ToolCallRequest) -> ToolMessage | None:
        tool_name = _request_tool_name(request)
        if tool_name not in DEVICE_ACTION_TOOLS:
            return None
        step_key = _device_step_key(request)
        if step_key is None:
            return None
        with self._action_claim_lock:
            first_tool = self._action_claims.get(step_key)
            if first_tool is not None:
                return _context_error(
                    request,
                    "multiple_device_actions",
                    f"Only the first device action in one model response is executed; {first_tool!r} already ran.",
                )
            self._action_claims[step_key] = tool_name
            self._action_claims.move_to_end(step_key)
            while len(self._action_claims) > 2048:
                self._action_claims.popitem(last=False)
        return None

    def _apply_transition(self, request: ToolCallRequest, result: Any) -> Any:
        state = _state(request)
        if not state.get(PAGE_CONTEXT_ACTIVE, False) or not _tool_succeeded(result):
            return result
        tool_name = _request_tool_name(request)
        args = _request_args(request)
        update: dict[str, Any] = {}

        if tool_name == "run_package":
            home_path = self.mechanism.home_path_for_package(str(args.get("package_name") or ""))
            update = _page_context_state(True, home_path, app_root=home_path, new_visit=True)
        elif tool_name == "close_package":
            update = _page_context_state(False, "")
        elif tool_name in {"navigate_to", "navigate_back"}:
            target = _canonical_path(args.get("next_path"))
            if is_compact_popup_result(result):
                update = {PAGE_CONTEXT_PENDING_PATH: target, PAGE_CONTEXT_OVERLAY: True}
            else:
                update = _page_visit_update(target)
        elif tool_name == "tap_in_page":
            if is_compact_popup_result(result):
                update = {PAGE_CONTEXT_OVERLAY: True}
            elif state.get(PAGE_CONTEXT_OVERLAY, False):
                pending = str(state.get(PAGE_CONTEXT_PENDING_PATH) or "")
                update = _page_visit_update(pending) if pending else {
                    PAGE_CONTEXT_PENDING_PATH: "",
                    PAGE_CONTEXT_OVERLAY: False,
                }
            elif self.mechanism.page_policy(str(state.get(PAGE_CONTEXT_PATH) or "")).role == "merchant":
                update = {PAGE_PRODUCT_ENTRY_TAPS: int(state.get(PAGE_PRODUCT_ENTRY_TAPS, 0)) + 1}
        elif tool_name in {"swipe_up", "swipe_down"}:
            if self.mechanism.page_policy(str(state.get(PAGE_CONTEXT_PATH) or "")).role == "merchant":
                update = {PAGE_COLLECTION_STARTED: True}
        elif tool_name == "uiautomate" and is_compact_popup_result(result):
            update = {PAGE_CONTEXT_OVERLAY: True}

        return _with_state_update(result, update) if update else result


def initial_page_context_state() -> dict[str, Any]:
    """Activate page context for an exact app-probe request."""

    return _page_context_state(True, "")


def _page_context_state(
    active: bool,
    path: str,
    *,
    app_root: str = "",
    new_visit: bool = False,
) -> dict[str, Any]:
    state = {
        PAGE_CONTEXT_ACTIVE: active,
        PAGE_CONTEXT_APP_ROOT: app_root,
        PAGE_CONTEXT_PATH: path,
        PAGE_CONTEXT_PENDING_PATH: "",
        PAGE_CONTEXT_OVERLAY: False,
        PAGE_VISIT_ID: uuid4().hex if new_visit and path else "",
        PAGE_PRODUCT_ENTRY_TAPS: 0,
        PAGE_COLLECTION_STARTED: False,
        **reset_collection_state(),
    }
    return state


def _page_visit_update(path: str) -> dict[str, Any]:
    return {
        PAGE_CONTEXT_PATH: path,
        PAGE_CONTEXT_PENDING_PATH: "",
        PAGE_CONTEXT_OVERLAY: False,
        PAGE_VISIT_ID: uuid4().hex,
        PAGE_PRODUCT_ENTRY_TAPS: 0,
        PAGE_COLLECTION_STARTED: False,
        **reset_collection_state(),
    }


def is_compact_popup_result(result: Any) -> bool:
    xml = _xml_from_result(result)
    if not xml:
        return False
    bounds = [tuple(map(int, match)) for match in BOUNDS_RE.findall(xml)]
    if len(bounds) < 2 or len(bounds) > 40 or ' k="1"' not in xml:
        return False
    left = min(item[0] for item in bounds)
    top = min(item[1] for item in bounds)
    right = max(item[2] for item in bounds)
    bottom = max(item[3] for item in bounds)
    return (
        max(0, right - left) / DEFAULT_VIEWPORT_WIDTH < 0.9
        or max(0, bottom - top) / DEFAULT_VIEWPORT_HEIGHT < 0.7
    )


def _state(request: Any) -> Mapping[str, Any]:
    state = getattr(request, "state", None)
    return state if isinstance(state, Mapping) else {}


def _tool_name(tool: Any) -> str:
    if isinstance(tool, Mapping):
        return str(tool.get("name") or tool.get("function", {}).get("name") or "")
    return str(getattr(tool, "name", "") or "")


def _tools_named(tools: Sequence[Any], names: set[str] | frozenset[str]) -> list[Any]:
    return [tool for tool in tools if _tool_name(tool) in names]


def _request_tool_name(request: Any) -> str:
    call = getattr(request, "tool_call", None)
    if isinstance(call, Mapping):
        return str(call.get("name") or "")
    return _tool_name(getattr(request, "tool", None))


def _request_args(request: Any) -> Mapping[str, Any]:
    call = getattr(request, "tool_call", None)
    args = call.get("args") if isinstance(call, Mapping) else None
    return args if isinstance(args, Mapping) else {}


def _canonical_path(value: Any) -> str:
    parts, error = normalize_page_path(str(value or ""))
    return "" if error else "/".join(parts)


def _device_step_key(request: Any) -> tuple[str, str] | None:
    state = _state(request)
    messages = state.get("messages") or []
    if not messages:
        return None
    latest = messages[-1]
    message_id = getattr(latest, "id", None)
    if message_id is None and isinstance(latest, Mapping):
        message_id = latest.get("id")
    if not message_id:
        tool_calls = getattr(latest, "tool_calls", None)
        if tool_calls is None and isinstance(latest, Mapping):
            tool_calls = latest.get("tool_calls")
        fingerprint_input = json.dumps(
            tool_calls or {"message_index": len(messages) - 1, "content": str(getattr(latest, "content", ""))},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        message_id = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
    runtime = getattr(request, "runtime", None)
    config = getattr(runtime, "config", None)
    configurable = config.get("configurable", {}) if isinstance(config, Mapping) else {}
    thread_id = str(configurable.get("thread_id") or "default") if isinstance(configurable, Mapping) else "default"
    return thread_id, str(message_id)


def _context_error(request: Any, error_type: str, detail: str) -> ToolMessage:
    call = getattr(request, "tool_call", None)
    call = call if isinstance(call, Mapping) else {}
    payload = {
        "ok": False,
        "error_type": error_type,
        "detail": detail,
        "page_path": str(_state(request).get(PAGE_CONTEXT_PATH) or ""),
    }
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        tool_call_id=str(call.get("id") or call.get("tool_call_id") or ""),
        name=str(call.get("name") or "unknown_tool"),
        status="error",
        additional_kwargs={"error_type": error_type},
    )


def _tool_succeeded(result: Any) -> bool:
    messages = _result_messages(result)
    if not messages:
        return False
    message = messages[-1]
    if getattr(message, "status", "success") == "error":
        return False
    content = getattr(message, "content", None)
    if isinstance(content, Mapping):
        return not bool(content.get("error_type") or content.get("ok") is False)
    if isinstance(content, str) and content.lstrip().startswith("{"):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return True
        if isinstance(payload, Mapping):
            return not bool(payload.get("error_type") or payload.get("ok") is False)
    return True


def _xml_from_result(result: Any) -> str:
    messages = _result_messages(result)
    if not messages:
        return ""
    content = getattr(messages[-1], "content", None)
    if not isinstance(content, str):
        return ""
    if content.lstrip().startswith("{"):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return ""
        return str(payload.get("xml") or "") if isinstance(payload, Mapping) else ""
    return content if "<h" in content or "<hierarchy" in content else ""


def _result_messages(result: Any) -> list[ToolMessage]:
    if isinstance(result, ToolMessage):
        return [result]
    if isinstance(result, Command) and isinstance(result.update, Mapping):
        messages = result.update.get("messages") or []
        return [message for message in messages if isinstance(message, ToolMessage)]
    return []


def _with_state_update(result: Any, state_update: Mapping[str, Any]) -> Command:
    if isinstance(result, ToolMessage):
        return Command(update={"messages": [result], **state_update})
    if isinstance(result, Command):
        update = dict(result.update) if isinstance(result.update, Mapping) else {}
        update.update(state_update)
        return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)
    raise TypeError(f"Unsupported successful tool result: {type(result).__name__}")


__all__ = [
    "PAGE_CONTEXT_ACTIVE",
    "PAGE_CONTEXT_APP_ROOT",
    "PAGE_CONTEXT_OVERLAY",
    "PAGE_CONTEXT_PATH",
    "PAGE_CONTEXT_PENDING_PATH",
    "PageContextMiddleware",
    "PageContextState",
    "initial_page_context_state",
]
