"""Thread-scoped page context and explicit navigation transitions."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Awaitable, Callable, Mapping, Sequence
import hashlib
import json
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
from langchain_core.messages import AIMessage, ToolMessage
from typing_extensions import NotRequired
from Reco.model_interface import TOOLS as PAGE_TOOLS

PAGE_TOOL_NAMES = frozenset(tool["name"] for tool in PAGE_TOOLS)

try:
    from src.middleware.probe_state import reset_collection_state
    from src.middleware.tool_io import (
        ToolInvocation,
        require_tool_result,
        tool_succeeded,
        with_state_update,
    )
    from src.page_mechanism import PageMechanism, normalize_page_path
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.probe_state import reset_collection_state
    from middleware.tool_io import (
        ToolInvocation,
        require_tool_result,
        tool_succeeded,
        with_state_update,
    )
    from page_mechanism import PageMechanism, normalize_page_path


PAGE_CONTEXT_ACTIVE = "page_context_active"
PAGE_CONTEXT_APP_ROOT = "page_context_app_root"
PAGE_CONTEXT_PATH = "page_context_path"
PAGE_CONTEXT_PENDING_PATH = "page_context_pending_path"
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
        "swipe_up_long",
        "swipe_down",
        "page_view",
        "screenshot",
    }
)


class PageContextMiddleware(AgentMiddleware[PageContextState]):
    """Validate page-scoped tool calls and apply explicit navigation transitions."""

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
        state = request.state
        if not state.get(PAGE_CONTEXT_ACTIVE, False):
            return request

        model_settings = dict(request.model_settings or {})
        model_settings["parallel_tool_calls"] = False
        return request.override(model_settings=model_settings)

    def _validate_tool_call(self, request: ToolCallRequest) -> ToolMessage | None:
        invocation = ToolInvocation.from_request(request)
        state = invocation.state
        if not state.get(PAGE_CONTEXT_ACTIVE, False):
            return None
        tool_name = invocation.name
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")

        if not current_path and tool_name in (DEVICE_ACTION_TOOLS | PAGE_TOOL_NAMES) - {"run_package", "close_package"}:
            return _context_error(request, "application_not_open", "Call run_package before page operations.")

        args = invocation.args
        if tool_name == "run_package":
            try:
                self.mechanism.home_path_for_package(str(args.get("package_name") or ""))
            except (ValueError, FileNotFoundError) as exc:
                return _context_error(request, "invalid_app_home", str(exc))
        if tool_name in {"navigate_to", "navigate_back"}:
            error = self._navigation_error(state, str(args.get("next_path") or ""))
            if error:
                return _context_error(request, "invalid_navigation_target", error)
        if tool_name == "tap_in_page" and args.get("purpose") != "dismiss_overlay":
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
        invocation = ToolInvocation.from_request(request)
        tool_name = invocation.name
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
        invocation = ToolInvocation.from_request(request)
        state = invocation.state
        typed_result = require_tool_result(result)
        if not state.get(PAGE_CONTEXT_ACTIVE, False) or not tool_succeeded(typed_result):
            return result
        tool_name = invocation.name
        args = invocation.args
        update: dict[str, Any] = {}

        if tool_name == "run_package":
            home_path = self.mechanism.home_path_for_package(str(args.get("package_name") or ""))
            update = _page_context_state(True, home_path, app_root=home_path, new_visit=True)
        elif tool_name == "close_package":
            update = _page_context_state(False, "")
        elif tool_name in {"navigate_to", "navigate_back"}:
            target = _canonical_path(args.get("next_path"))
            update = _page_visit_update(target)
        elif tool_name == "tap_in_page":
            if args.get("purpose") != "dismiss_overlay" and self.mechanism.page_policy(str(state.get(PAGE_CONTEXT_PATH) or "")).role == "merchant":
                update = {PAGE_PRODUCT_ENTRY_TAPS: int(state.get(PAGE_PRODUCT_ENTRY_TAPS, 0)) + 1}
        elif tool_name in {"swipe_up", "swipe_up_long", "swipe_down"}:
            if self.mechanism.page_policy(str(state.get(PAGE_CONTEXT_PATH) or "")).role == "merchant":
                update = {PAGE_COLLECTION_STARTED: True}

        return with_state_update(typed_result, update) if update else result


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
        PAGE_VISIT_ID: uuid4().hex,
        PAGE_PRODUCT_ENTRY_TAPS: 0,
        PAGE_COLLECTION_STARTED: False,
        **reset_collection_state(),
    }


def _canonical_path(value: Any) -> str:
    parts, error = normalize_page_path(str(value or ""))
    return "" if error else "/".join(parts)


def _device_step_key(request: Any) -> tuple[str, str] | None:
    invocation = ToolInvocation.from_request(request)
    state = invocation.state
    messages = state.get("messages") or []
    if not messages:
        return None
    latest = messages[-1]
    message_id = latest.get("id") if isinstance(latest, Mapping) else latest.id
    if not message_id:
        tool_calls = latest.get("tool_calls") if isinstance(latest, Mapping) else (
            latest.tool_calls if isinstance(latest, AIMessage) else None
        )
        content = latest.get("content", "") if isinstance(latest, Mapping) else latest.content
        fingerprint_input = json.dumps(
            tool_calls or {"message_index": len(messages) - 1, "content": str(content)},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        message_id = hashlib.sha256(fingerprint_input.encode("utf-8")).hexdigest()
    configurable = request.runtime.config.get("configurable", {})
    thread_id = str(configurable.get("thread_id") or "default") if isinstance(configurable, Mapping) else "default"
    return thread_id, str(message_id)


def _context_error(request: Any, error_type: str, detail: str) -> ToolMessage:
    invocation = ToolInvocation.from_request(request)
    payload = {
        "ok": False,
        "error_type": error_type,
        "detail": detail,
        "page_path": str(invocation.state.get(PAGE_CONTEXT_PATH) or ""),
    }
    return ToolMessage(
        content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        tool_call_id=invocation.call_id,
        name=invocation.name or "unknown_tool",
        status="error",
        additional_kwargs={"error_type": error_type},
    )


__all__ = [
    "PAGE_CONTEXT_ACTIVE",
    "PAGE_CONTEXT_APP_ROOT",
    "PAGE_CONTEXT_PATH",
    "PAGE_CONTEXT_PENDING_PATH",
    "PageContextMiddleware",
    "PageContextState",
    "initial_page_context_state",
]
