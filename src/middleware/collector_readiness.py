"""Warn about untrained visits without preventing page recovery operations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ToolCallRequest,
)
from Reco.model_interface import TOOLS as PAGE_TOOLS

try:
    from src.middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from src.middleware.tool_io import ToolInvocation, with_warning
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from middleware.tool_io import ToolInvocation, with_warning
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
    """Advise caution until the current visit passes collector training."""

    def __init__(
        self,
        *,
        mechanism: PageMechanism,
        training_registry: CollectorTrainingRegistry,
    ) -> None:
        self.mechanism = mechanism
        self.training_registry = training_registry

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]) -> Any:
        warning = self._warning_required(request)
        result = handler(request)
        return self._warn(request, result) if warning else result

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        warning = self._warning_required(request)
        result = await handler(request)
        return self._warn(request, result) if warning else result

    def _warn(self, request, result):
        return with_warning(result, ToolInvocation.from_request(request), "collector_training_required",
                            "The collector is not ready for this visit. Proceed cautiously; automatic collection "
                            "is unavailable until retraining succeeds. Inspect, repair baselines, or leave and re-enter for recovery.")

    def _warning_required(self, request: ToolCallRequest) -> bool:
        invocation = ToolInvocation.from_request(request)
        if not self._requires_training(invocation.state):
            return False
        allowed = set(UNTRAINED_ALLOWED_TOOLS) | {"screenshot"}
        allowed.update(tool["name"] for tool in PAGE_TOOLS)
        if invocation.args.get("purpose") == "dismiss_overlay":
            allowed.add("tap_in_page")
        return invocation.name not in allowed

    def _requires_training(self, state: Mapping[str, Any]) -> bool:
        path = str(state.get(PAGE_CONTEXT_PATH) or "")
        visit_id = str(state.get(PAGE_VISIT_ID) or "")
        policy = self.mechanism.page_policy(path)
        record = self.training_registry.record_for(visit_id)
        stale = False
        if record and record.contract_fingerprint:
            contract = self.mechanism.collection_contract(path)
            stale = contract is None or contract.fingerprint != record.contract_fingerprint
            if stale:
                self.training_registry.mark_untrained(
                    visit_id, path, merchant_profile=record.merchant_profile,
                    script_fingerprint=record.script_fingerprint, restoration=record.restoration,
                )
        return bool(
            policy.requires_collector
            and (stale or not visit_id or not self.training_registry.is_trained(visit_id, path))
        )


__all__ = ["CollectorReadinessMiddleware", "UNTRAINED_ALLOWED_TOOLS"]
