"""Require instance-scoped collector training before merchant page operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
import json
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage

try:
    from src.middleware.page_context import PAGE_CONTEXT_OVERLAY, PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.page_context import PAGE_CONTEXT_OVERLAY, PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from page_mechanism import CollectorTrainingRegistry, PageMechanism


UNTRAINED_VISIBLE_TOOLS = frozenset(
    {
        "prepare_collector",
        "navigate_back",
        "close_package",
        "notify_user",
        "think",
        "authenticate_captcha",
        "captcha_authenticated",
    }
)


class CollectorReadinessMiddleware(AgentMiddleware):
    """Gate a concrete collector-required page visit until its script passes."""

    def __init__(
        self,
        *,
        mechanism: PageMechanism,
        training_registry: CollectorTrainingRegistry,
    ) -> None:
        self.mechanism = mechanism
        self.training_registry = training_registry

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
        rejection = self._rejection(request)
        return rejection if rejection is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        rejection = self._rejection(request)
        return rejection if rejection is not None else await handler(request)

    def _prepare_model_request(self, request: ModelRequest) -> ModelRequest:
        state = _state(request)
        if not self._requires_training(state):
            return request
        allowed = set(UNTRAINED_VISIBLE_TOOLS)
        if state.get(PAGE_CONTEXT_OVERLAY, False):
            allowed.add("tap_in_page")
        return request.override(tools=_tools_named(request.tools, allowed))

    def _rejection(self, request: ToolCallRequest) -> ToolMessage | None:
        state = _state(request)
        if not self._requires_training(state):
            return None
        tool_name = _request_tool_name(request)
        allowed = set(UNTRAINED_VISIBLE_TOOLS)
        if state.get(PAGE_CONTEXT_OVERLAY, False):
            allowed.add("tap_in_page")
        if tool_name in allowed:
            return None
        call = request.tool_call if isinstance(request.tool_call, Mapping) else {}
        return ToolMessage(
            content=json.dumps(
                {
                    "ok": False,
                    "error_type": "collector_training_required",
                    "page_path": str(state.get(PAGE_CONTEXT_PATH) or ""),
                    "page_visit_id": str(state.get(PAGE_VISIT_ID) or ""),
                    "required_tool": "prepare_collector",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            tool_call_id=str(call.get("id") or "collector-readiness"),
            name=tool_name or None,
            status="error",
        )

    def _requires_training(self, state: Mapping[str, Any]) -> bool:
        path = str(state.get(PAGE_CONTEXT_PATH) or "")
        visit_id = str(state.get(PAGE_VISIT_ID) or "")
        policy = self.mechanism.page_policy(path)
        return bool(
            policy.requires_collector
            and (not visit_id or not self.training_registry.is_trained(visit_id, path))
        )


def _state(request: Any) -> Mapping[str, Any]:
    state = getattr(request, "state", None)
    return state if isinstance(state, Mapping) else {}


def _request_tool_name(request: Any) -> str:
    call = getattr(request, "tool_call", None)
    if isinstance(call, Mapping):
        return str(call.get("name") or "")
    return str(getattr(getattr(request, "tool", None), "name", "") or "")


def _tool_name(tool: Any) -> str:
    if isinstance(tool, Mapping):
        return str(tool.get("name") or tool.get("function", {}).get("name") or "")
    return str(getattr(tool, "name", "") or "")


def _tools_named(tools: Sequence[Any], names: set[str]) -> list[Any]:
    return [tool for tool in tools if _tool_name(tool) in names]


__all__ = ["CollectorReadinessMiddleware", "UNTRAINED_VISIBLE_TOOLS"]
