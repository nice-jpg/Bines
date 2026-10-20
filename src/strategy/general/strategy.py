"""Common page behavior without collector or merchant workflow assumptions."""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AnyMessage, SystemMessage
from langgraph.runtime import Runtime

from src.agent_messages.tool_results import ToolResult
from src.agent_runtime.strategy.base import Strategy
from src.conductor.state import ConductorState
from src.strategy.general.captcha import CaptchaHumanInTheLoopMiddleware
from src.strategy.general.context.middleware import PageInputMiddleware
from src.strategy.general.reliability import DeviceOperationReliabilityMiddleware
from src.strategy.general.state import PendingVisit
from src.strategy.general.visits import MERCHANT_TOOLS, PageVisitController, reject


class GeneralStrategy(Strategy[ConductorState]):
    def __init__(
        self,
        controller: PageVisitController,
        context: PageInputMiddleware,
        reliability: DeviceOperationReliabilityMiddleware,
        captcha: CaptchaHumanInTheLoopMiddleware,
    ) -> None:
        self.controller: PageVisitController = controller
        self.context: PageInputMiddleware = context
        self.reliability: DeviceOperationReliabilityMiddleware = reliability
        self.captcha: CaptchaHumanInTheLoopMiddleware = captcha

    def before_model(self, state: ConductorState, runtime: Runtime[None]) -> dict[str, object] | None:
        pending: PendingVisit | None = state["general"].get("pending")
        if pending is not None:
            content: str = self.controller.mechanism.load_manual_bundle(pending["path"])
            return {
                "messages": [
                    SystemMessage(
                        content=f'<candidate_page_manual candidate_id="{pending["candidate_id"]}">\nThis is an UNCONFIRMED destination. Observe the current device and use confirm_page; do not train or collect yet.\n{content.replace("current_page_manual", "candidate_manual")}\n</candidate_page_manual>',
                        id=f"candidate-manual:{pending['candidate_id']}",
                    )
                ]
            }
        update: dict[str, object] = {}
        manual: dict[str, list[AnyMessage]] | None = self.context.before_model(state, runtime)
        if manual is not None:
            update.update(manual)
        return update or None

    def after_model(self, state: ConductorState, runtime: Runtime[None]) -> dict[str, object] | None:
        return self.captcha.after_model(state, runtime)

    async def aafter_model(self, state: ConductorState, runtime: Runtime[None]) -> dict[str, object] | None:
        return await self.captcha.aafter_model(state, runtime)

    def wrap_model_call[T](
        self, request: ModelRequest[None], handler: Callable[[ModelRequest[None]], ModelResponse[T]]
    ) -> ModelResponse[T]:
        return self.context.wrap_model_call(
            request.override(model_settings={**request.model_settings, "parallel_tool_calls": False}), handler
        )

    async def awrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[T]]],
    ) -> ModelResponse[T]:
        return await self.context.awrap_model_call(
            request.override(model_settings={**request.model_settings, "parallel_tool_calls": False}), handler
        )

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        if request.tool_call["name"] in MERCHANT_TOOLS:
            return reject(
                request,
                "page_strategy_not_applicable",
                "Collector operations require a confirmed merchant page.",
            )
        return self.execute_common(request, handler)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        if request.tool_call["name"] in MERCHANT_TOOLS:
            return reject(
                request,
                "page_strategy_not_applicable",
                "Collector operations require a confirmed merchant page.",
            )
        return await self.aexecute_common(request, handler)

    def execute_common(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        return self.controller.execute_operation(
            request, lambda inner: self.reliability.wrap_tool_call(inner, handler)
        )

    async def aexecute_common(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        async def reliable(inner: ToolCallRequest) -> ToolResult:
            return await self.reliability.awrap_tool_call(inner, handler)

        return await self.controller.aexecute_operation(request, reliable)
