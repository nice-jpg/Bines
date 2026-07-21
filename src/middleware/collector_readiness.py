"""Require instance-scoped collector training before merchant page operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import json
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ToolCallRequest,
)
from langchain_core.messages import ToolMessage

try:
    from src.middleware.page_context import PAGE_CONTEXT_OVERLAY, PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from src.middleware.tool_io import ToolInvocation
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.page_context import PAGE_CONTEXT_OVERLAY, PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from middleware.tool_io import ToolInvocation
    from page_mechanism import CollectorTrainingRegistry, PageMechanism


UNTRAINED_ALLOWED_TOOLS = frozenset(
    {
        "prepare_collector",
        "navigate_back",
        "close_package",
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

    def _rejection(self, request: ToolCallRequest) -> ToolMessage | None:
        invocation = ToolInvocation.from_request(request)
        state = invocation.state
        if not self._requires_training(state):
            return None
        tool_name = invocation.name
        allowed = set(UNTRAINED_ALLOWED_TOOLS)
        if state.get(PAGE_CONTEXT_OVERLAY, False):
            allowed.add("tap_in_page")
        if tool_name in allowed:
            return None
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
            tool_call_id=invocation.call_id or "collector-readiness",
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


__all__ = ["CollectorReadinessMiddleware", "UNTRAINED_ALLOWED_TOOLS"]
