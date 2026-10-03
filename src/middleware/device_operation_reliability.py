"""Verify selected device operations by comparing page snapshots."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

if __package__ and __package__.startswith("src."):
    from src.middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from src.middleware.tool_io import ToolInvocation, require_tool_result, page_xml, tool_succeeded, with_warning
    from src.tools.event_hub import EventHub
    from src.tools.event_logger import WorkspaceEventLogger
    from src.tools.operation_effect import OperationEffectPolicy, operation_effect_policy_from_metadata
    from src.tools.page_similarity import PageSimilarityError, PageSimilarityEvaluator
else:  # Supports running tests and python src/run_agent.py with src on sys.path.
    from middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
    from middleware.tool_io import ToolInvocation, require_tool_result, page_xml, tool_succeeded, with_warning
    from tools.event_hub import EventHub
    from tools.event_logger import WorkspaceEventLogger
    from tools.operation_effect import OperationEffectPolicy, operation_effect_policy_from_metadata
    from tools.page_similarity import PageSimilarityError, PageSimilarityEvaluator


class DeviceOperationReliabilityMiddleware(AgentMiddleware):
    """Warn when configured device operations do not produce the expected change."""

    def __init__(self, *, event_hub: EventHub) -> None:
        self.event_hub = event_hub
        self.evaluator = PageSimilarityEvaluator()
        self.logger = WorkspaceEventLogger()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Any],
    ) -> Any:
        invocation = ToolInvocation.from_request(request)
        policy = operation_effect_policy_from_metadata(invocation.tool_metadata)
        if policy is None:
            return handler(request)

        before_xml = self._before_xml()
        result = handler(request)
        return self._evaluate(request, result, before_xml, policy)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[Any]],
    ) -> Any:
        invocation = ToolInvocation.from_request(request)
        policy = operation_effect_policy_from_metadata(invocation.tool_metadata)
        if policy is None:
            return await handler(request)

        before_xml = self._before_xml()
        result = await handler(request)
        return self._evaluate(request, result, before_xml, policy)

    def _evaluate(
        self,
        request: ToolCallRequest,
        result: Any,
        before_xml: str,
        policy: OperationEffectPolicy,
    ) -> Any:
        if not isinstance(result, (ToolMessage, Command)):
            return result
        typed_result = require_tool_result(result)
        if not tool_succeeded(typed_result):
            return result
        if not before_xml:
            return self._unverifiable(request, result, "device_operation_precheck_failed")
        after_xml = _result_xml(typed_result)
        if not after_xml:
            return self._unverifiable(request, result, "device_operation_postcheck_failed")
        try:
            comparison = self.evaluator.compare(before_xml, after_xml)
        except PageSimilarityError:
            return self._unverifiable(request, result, "device_operation_effect_unverifiable")

        effective = policy.accepts(comparison.similarity)
        self._log_result(
            request,
            similarity=comparison.similarity,
            effective=effective,
            error_type="" if effective else "device_operation_no_effect",
        )
        if effective:
            return result
        return with_warning(
            typed_result, ToolInvocation.from_request(request), "device_operation_effect_warning",
            f"操作后页面相似度{'较高' if comparison.similarity > 0.5 else '较低'}"
            f"({comparison.similarity:.1%})。请根据页面判断操作效果；该提示不代表操作失败。",
        )

    def _before_xml(self) -> str:
        try:
            _, snapshot = self.event_hub.page_bridge.refresh()
            return snapshot["page_xml"] if snapshot else ""
        except Exception:
            return ""

    def _unverifiable(self, request, result, error_type):
        self._log_result(request, similarity=None, effective=False, error_type=error_type)
        return with_warning(require_tool_result(result), ToolInvocation.from_request(request),
                            error_type, "无法比较操作前后的页面；请根据当前视图或截图确认操作效果。")

    def _log_result(
        self,
        request: ToolCallRequest,
        *,
        similarity: float | None,
        effective: bool,
        error_type: str,
    ) -> None:
        invocation = ToolInvocation.from_request(request)
        self.logger.log(
            "device_operation_effect_evaluated",
            "Device operation effect evaluated.",
            {
                "tool_name": invocation.name,
                "page_path": str(invocation.state.get(PAGE_CONTEXT_PATH) or ""),
                "page_visit_id": str(invocation.state.get(PAGE_VISIT_ID) or ""),
                "similarity": similarity,
                "effective": effective,
                "error_type": error_type,
            },
        )


def _result_xml(result: ToolMessage | Command) -> str:
    return page_xml(result)


__all__ = ["DeviceOperationReliabilityMiddleware"]
