"""Strict collector-page workflow, isolated from general page operations."""

from collections.abc import Awaitable, Callable
from typing import Final

from langchain.agents.middleware.types import ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import AIMessage, AnyMessage, SystemMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

from src.agent_messages.tool_results import ToolResult
from src.agent_runtime.strategy.base import Strategy
from src.conductor.state import (
    ConductorFields,
    ConductorState,
    ConductorUpdate,
    combine_updates,
    complete_update,
    current_visit,
    merge_fields,
    reset_collection_state,
)
from src.middleware.tool_io import ToolInvocation, command_update, tool_succeeded, with_state_update
from src.strategy.general.state import PageVisit
from src.strategy.general.strategy import GeneralStrategy
from src.strategy.general.visits import NAVIGATION_TOOLS, OBSERVATION_TOOLS, reject
from src.strategy.merchant.persistence import CollectionPersistenceMiddleware
from src.strategy.merchant.state import (
    MERCHANT_AWAITING_TRAINING,
    MERCHANT_COMPLETED,
    MERCHANT_READY,
    MERCHANT_TRAVERSING,
    MerchantState,
    begin_merchant_visit,
    initial_merchant_state,
    is_merchant_visit,
)
from src.tools.collection.service import CollectionService

MERCHANT_INSTRUCTIONS: Final[str] = (
    "6. Merchant collection pages require collector training for every concrete page visit. Call prepare_collector before operating that page, with page_path, page structure/function, merchant_schema, and collection_schema from PAGE.md. Do not include task.md operation steps.\n7. After collector readiness, operate the page according to task.md.\n   Check trained, restoration and normal_swipe_profile. Once the product area is active and its safe swipe\n   coordinates are known, pass the exact returned change_threshold to collect_continuously. It performs serial\n   long swipes, short-swipes incomplete rows and returns when complete or attention is needed. It also yields\n   for incoming user input or shutdown. Do not issue per-step collection instructions while it is running.\n   A missing normal_swipe_profile requires collector sampling before automatic collection, but manual tools remain available.\n   On attention_required inspect the current page before recovery; never blindly repeat an abnormal gesture or raise the threshold.\n   On yielded incorporate the new user message. Only resume automatic collection if still appropriate.\n   Untrained product operations are blocked; page-similarity warnings remain advisory.\n   Inspect the current view before choosing a recovery action. No automatic collection occurs before training.\n   To leave an unfinished merchant for repair/re-entry, provide recovery_reason to navigate_back/navigate_to.\n   Recovery never counts as completion: return to the same merchant and finish it before authorizing the probe.\n   Ask collector to repair a proven bad baseline from the original screenshot rather than deleting cases or restarting blindly.\n\n- On merchant pages, use at most one tap_in_page before the first product-list swipe, and only when a product tab must be opened. Once scrolling starts, category-anchor taps are runtime-blocked; traverse products continuously with swipes.\n- Prefer collect_continuously after training. Its completed result uses the same three-zero-swipe completion rule as is_done and authorizes navigate_back without another is_done call. With manual tools, do not call is_done after ordinary swipes: check only on likely exhaustion. If done=false, continue and wait for new possible-end evidence. attention_required and yielded never authorize completion by themselves.\n\nUnlike the old advisory behavior, untrained product operations are blocked. Retrain when instructed; observation, communication and recovery remain available."
)

SWIPES: frozenset[str] = frozenset({"swipe_up", "swipe_up_long", "swipe_down", "collect_continuously"})


class MerchantStrategy(Strategy[ConductorState]):
    def __init__(self, common: GeneralStrategy, persistence: CollectionPersistenceMiddleware) -> None:
        self.common: GeneralStrategy = common
        self.persistence: CollectionPersistenceMiddleware = persistence
        self.service: CollectionService = persistence.service

    @staticmethod
    def begin_visit(visit: PageVisit) -> MerchantState:
        return begin_merchant_visit(visit)

    @staticmethod
    def end_visit() -> MerchantState:
        return initial_merchant_state()

    def before_model(self, state: ConductorState, runtime: Runtime[None]) -> dict[str, object] | None:
        update: dict[str, object] = self.common.before_model(state, runtime) or {}
        visit: PageVisit | None = state["general"].get("visit")
        if visit is not None and is_merchant_visit(visit) and state["general"].get("pending") is None:
            if state["merchant"].get("phase") != MERCHANT_AWAITING_TRAINING and self._training_error(
                state, state["merchant"]
            ):
                update.update(self._invalidate(state, state["merchant"]))
                phase: str = MERCHANT_AWAITING_TRAINING
            else:
                phase = state["merchant"].get("phase", "inactive")
            messages: dict[str, list[AnyMessage]] | None = self.common.context.before_model(state, runtime)
            update["messages"] = [
                *(messages["messages"] if messages else []),
                SystemMessage(
                    content=f"Merchant collection phase: {phase}.\n{MERCHANT_INSTRUCTIONS}",
                    id=f"merchant-phase:{current_visit(state).get('visit_id', '')}",
                ),
            ]
        return update or None

    def after_model(self, state: ConductorState, runtime: Runtime[None]) -> dict[str, object] | None:
        return self._training_start(state, self.common.after_model(state, runtime))

    async def aafter_model(self, state: ConductorState, runtime: Runtime[None]) -> dict[str, object] | None:
        return self._training_start(state, await self.common.aafter_model(state, runtime))

    def _training_start(
        self, state: ConductorState, update: dict[str, object] | None
    ) -> dict[str, object] | None:
        visit: PageVisit | None = state["general"].get("visit")
        latest: AIMessage | None = next(
            (message for message in reversed(state["messages"]) if isinstance(message, AIMessage)), None
        )
        if (
            state["general"].get("pending") is None
            and visit is not None
            and is_merchant_visit(visit)
            and latest is not None
            and any(call["name"] == "prepare_collector" for call in latest.tool_calls)
        ):
            return {**(update or {}), **self._invalidate(state, state["merchant"])}
        return update

    def wrap_model_call[T](
        self, request: ModelRequest[None], handler: Callable[[ModelRequest[None]], ModelResponse[T]]
    ) -> ModelResponse[T]:
        return self.common.wrap_model_call(request, handler)

    async def awrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[T]]],
    ) -> ModelResponse[T]:
        return await self.common.awrap_model_call(request, handler)

    def _training_error(self, state: ConductorFields, visit: MerchantState) -> str:
        if visit.get("visit_id") != current_visit(state)["visit_id"]:
            return "merchant_visit_mismatch"
        error: str = self.service.training_error(state)
        if error:
            return error
        if visit.get("training_version") != self.service.training_version(state):
            return "training_version_changed"
        return ""

    def _invalidate(self, state: ConductorFields, visit: MerchantState) -> ConductorUpdate:
        completed: dict[str, tuple[str, ...]] = dict(state["probe"].get("completed_merchants", {}))
        name: str = state["probe"].get("current_merchant", "")
        completed[current_visit(state).get("path", "")] = tuple(
            item for item in completed.get(current_visit(state).get("path", ""), ()) if item != name
        )
        return complete_update(
            state,
            combine_updates(
                {
                    "merchant": {
                        **visit,
                        "phase": MERCHANT_AWAITING_TRAINING,
                        "training_version": "",
                    },
                    "probe": {"completed_merchants": completed},
                },
                reset_collection_state(),
            ),
        )

    def _validate(self, request: ToolCallRequest) -> ToolResult | None:
        call: ToolInvocation = ToolInvocation.from_request(request)
        if call.state["general"].get("pending") is not None:
            return None  # Common pending-arrival rules take precedence over source strategy.
        visit: PageVisit | None = call.state["general"].get("visit")
        if visit is None or not is_merchant_visit(visit):
            return reject(request, "invalid_strategy_state", "A confirmed merchant visit is required.")
        if call.name == "prepare_collector":
            return None
        error: str = (
            self._training_error(call.state, call.state["merchant"])
            if call.state["merchant"].get("phase") != MERCHANT_AWAITING_TRAINING
            else "collector_not_trained"
        )
        safe: bool = call.name in OBSERVATION_TOOLS | {
            "think",
            "talk",
            "authenticate_captcha",
            "captcha_authenticated",
            "update_page_notes",
        } or (call.name == "tap_in_page" and call.args.get("purpose") == "dismiss_overlay")
        if call.name in NAVIGATION_TOOLS:
            if (
                call.name != "run_package"
                and (call.state["merchant"].get("phase") != MERCHANT_COMPLETED or error)
                and call.args.get("recovery_reason")
            ):
                return None
            if call.state["merchant"].get("phase") != MERCHANT_COMPLETED or error:
                return with_state_update(
                    reject(
                        request,
                        "merchant_collection_incomplete",
                        "Complete merchant collection or supply recovery_reason for navigation.",
                    ),
                    self._invalidate(call.state, call.state["merchant"]) if error else {},
                )
        if safe:
            return None
        if error:
            return with_state_update(
                reject(request, error, "Retrain this visit before product operations."),
                self._invalidate(call.state, call.state["merchant"]),
            )
        if call.state["merchant"].get("phase") == MERCHANT_COMPLETED and call.name not in NAVIGATION_TOOLS | {
            "is_done"
        }:
            return reject(
                request,
                "structured_task_completed",
                "Leave the completed page or explicitly retrain before changing it.",
            )
        if call.name == "tap_in_page" and (
            call.state["merchant"].get("phase") == MERCHANT_TRAVERSING
            or call.state["merchant"].get("entry_taps", 0) >= 1
        ):
            return reject(
                request, "merchant_entry_tap_limit", "Only one product-entry tap is allowed before traversal."
            )
        return None

    def _after(self, request: ToolCallRequest, result: ToolResult) -> ToolResult:
        call: ToolInvocation = ToolInvocation.from_request(request)
        if call.state["general"].get("pending") is not None:
            return result
        visit: PageVisit | None = call.state["general"].get("visit")
        if visit is None or not is_merchant_visit(visit):
            return result
        merged: ConductorFields = merge_fields(
            call.state, command_update(result) if isinstance(result, Command) else {}
        )
        updated: MerchantState = merged["merchant"].copy()
        update: ConductorUpdate = {}
        if call.name == "prepare_collector":
            update = self._invalidate(merged, merged["merchant"])
            updated = merge_fields(merged, update)["merchant"]
            if not self.service.training_error(call.state) and tool_succeeded(result):
                updated.update(
                    phase=MERCHANT_READY,
                    training_version=self.service.training_version(call.state),
                    entry_taps=0,
                )
        elif call.name in SWIPES:
            # Evidence emitted by persistence proves a successful traversal; an early yield does not.
            if updated.get("evidence", {}).get("initialized") and updated.get("phase") != MERCHANT_COMPLETED:
                updated["phase"] = MERCHANT_TRAVERSING
            if not tool_succeeded(result):
                updated["evidence"] = {**updated.get("evidence", {}), "consecutive_empty_swipes": 0}
        elif (
            call.name == "tap_in_page"
            and call.args.get("purpose") != "dismiss_overlay"
            and tool_succeeded(result)
        ):
            updated["entry_taps"] = updated.get("entry_taps", 0) + 1
        update["merchant"] = updated
        return with_state_update(result, complete_update(merged, update))

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        def operate(inner: ToolCallRequest) -> ToolResult:
            rejection: ToolResult | None = self._validate(inner)
            if rejection is not None:
                return rejection
            result: ToolResult = (
                self.persistence.wrap_tool_call(inner, handler)
                if ToolInvocation.from_request(inner).state["general"].get("pending") is None
                else handler(inner)
            )
            return self._after(inner, result)

        return self.common.execute_common(request, operate)

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        async def operate(inner: ToolCallRequest) -> ToolResult:
            rejection: ToolResult | None = self._validate(inner)
            if rejection is not None:
                return rejection
            result: ToolResult = (
                await self.persistence.awrap_tool_call(inner, handler)
                if ToolInvocation.from_request(inner).state["general"].get("pending") is None
                else await handler(inner)
            )
            return self._after(inner, result)

        return await self.common.aexecute_common(request, operate)
