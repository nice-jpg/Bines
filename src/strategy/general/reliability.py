"""Verify selected device operations by comparing page snapshots."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp
from langgraph.types import Command

from src.agent_messages.tool_results import ToolResult
from src.common.event_log import WorkspaceEventLogger
from src.conductor.state import current_visit
from src.device import (
    DeviceOperationService,
    PageSimilarityError,
    PageSimilarityEvaluator,
    PageSimilarityResult,
)
from src.strategy.tool_io import ToolInvocation, page_xml, tool_succeeded, with_warning
from src.tools.device.effect import OperationEffectPolicy, operation_effect_policy_from_metadata


class DeviceOperationReliabilityMiddleware(AgentMiddleware):
    """Warn when configured device operations do not produce the expected change."""

    def __init__(self, *, event_hub: DeviceOperationService) -> None:
        self.event_hub: DeviceOperationService = event_hub
        self.evaluator: PageSimilarityEvaluator = PageSimilarityEvaluator()
        self.logger: WorkspaceEventLogger = WorkspaceEventLogger()

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        invocation: ToolInvocation = ToolInvocation.from_request(request)
        policy: OperationEffectPolicy | None = operation_effect_policy_from_metadata(invocation.tool_metadata)
        if policy is None:
            return handler(request)

        before_xml: str = self._before_xml()
        result: ToolResult = handler(request)
        return self._evaluate_safely(request, result, before_xml, policy)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        invocation: ToolInvocation = ToolInvocation.from_request(request)
        policy: OperationEffectPolicy | None = operation_effect_policy_from_metadata(invocation.tool_metadata)
        if policy is None:
            return await handler(request)

        before_xml: str = await asyncio.to_thread(self._before_xml)
        result: ToolResult = await handler(request)
        return self._evaluate_safely(request, result, before_xml, policy)

    def _evaluate_safely(
        self, request: ToolCallRequest, result: ToolResult, before_xml: str, policy: OperationEffectPolicy
    ) -> ToolResult:
        try:
            return self._evaluate(request, result, before_xml, policy)
        except GraphBubbleUp:
            raise
        except Exception:
            logging.getLogger(__name__).exception("Operation diagnostics failed; preserving device result")
            return result

    def _evaluate(
        self,
        request: ToolCallRequest,
        result: ToolResult,
        before_xml: str,
        policy: OperationEffectPolicy,
    ) -> ToolResult:
        typed_result: ToolResult = result
        if not tool_succeeded(typed_result):
            return result
        if not before_xml:
            return self._unverifiable(request, result, "device_operation_precheck_failed")
        after_xml: str = _result_xml(typed_result)
        if not after_xml:
            return self._unverifiable(request, result, "device_operation_postcheck_failed")
        try:
            comparison: PageSimilarityResult = self.evaluator.compare(before_xml, after_xml)
        except PageSimilarityError:
            return self._unverifiable(request, result, "device_operation_effect_unverifiable")

        effective: bool = policy.accepts(comparison.similarity)
        self._log_result(
            request,
            similarity=comparison.similarity,
            effective=effective,
            error_type="" if effective else "device_operation_no_effect",
        )
        if effective:
            return result
        return with_warning(
            typed_result,
            ToolInvocation.from_request(request),
            "device_operation_effect_warning",
            f"操作后页面相似度{'较高' if comparison.similarity > 0.5 else '较低'}"
            f"({comparison.similarity:.1%})。请根据页面判断操作效果；该提示不代表操作失败。",
        )

    def _before_xml(self) -> str:
        try:
            _, snapshot = self.event_hub.page_bridge.refresh()
            return snapshot["page_xml"] if snapshot else ""
        except GraphBubbleUp:
            raise
        except Exception:
            return ""

    def _unverifiable(self, request: ToolCallRequest, result: ToolResult, error_type: str) -> ToolResult:
        self._log_result(request, similarity=None, effective=False, error_type=error_type)
        return with_warning(
            result,
            ToolInvocation.from_request(request),
            error_type,
            "无法比较操作前后的页面；请根据当前视图或截图确认操作效果。",
        )

    def _log_result(
        self,
        request: ToolCallRequest,
        *,
        similarity: float | None,
        effective: bool,
        error_type: str,
    ) -> None:
        invocation: ToolInvocation = ToolInvocation.from_request(request)
        self.logger.log(
            "device_operation_effect_evaluated",
            "Device operation effect evaluated.",
            {
                "tool_name": invocation.name,
                "page_path": str(current_visit(invocation.state).get("path") or ""),
                "page_visit_id": str(current_visit(invocation.state).get("visit_id") or ""),
                "similarity": similarity,
                "effective": effective,
                "error_type": error_type,
            },
        )


def _result_xml(result: ToolMessage | Command[str]) -> str:
    return page_xml(result)


__all__: list[str] = ["DeviceOperationReliabilityMiddleware"]
