"""Page identity transitions, independent of collection and task completion."""

from collections.abc import Awaitable, Callable
from typing import cast
from uuid import uuid4

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.agent_messages.tool_results import ToolResult
from src.conductor.state import ConductorState, ConductorUpdate, complete_update
from src.page.mechanism import PageMechanism
from src.strategy.general.state import PageArrival, PageVisit, initial_general_state, unconfirmed_arrival
from src.strategy.general.visits import (
    NAVIGATION_TOOLS,
    OBSERVATION_TOOLS,
    PAGE_OPERATIONS,
    PageTarget,
    reject,
    resolve_page_target,
    validate_arrival,
)
from src.strategy.tool_io import (
    ToolInvocation,
    tool_succeeded,
    with_state_update,
    with_warning,
)


class PageVisitMiddleware(AgentMiddleware[ConductorState, None]):
    def __init__(self, mechanism: PageMechanism, strategy_names: frozenset[str]) -> None:
        self.mechanism: PageMechanism = mechanism
        self._strategy_names: frozenset[str] = strategy_names

    def _resolve_navigation_target(self, call: ToolInvocation) -> PageTarget:
        path: str = (
            self.mechanism.home_path_for_package(cast(str, call.args["package_name"]))
            if call.name == "run_package"
            else cast(str, call.args["next_path"])
        )
        target: PageTarget = resolve_page_target(self.mechanism, self._strategy_names, path)
        root: str = call.state["general"].get("app_root", "")
        if call.name != "run_package" and root and root != target.app_root:
            raise ValueError("Navigation target belongs to a different application.")
        return target

    def wrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        call: ToolInvocation = ToolInvocation.from_request(request)
        if self._requires_page_confirmation(call):
            return reject(
                request,
                "page_confirmation_required",
                "Call confirm_page with your conclusion before another page operation.",
            )
        if call.name in NAVIGATION_TOOLS:
            target: PageTarget = self._resolve_navigation_target(call)
            result: ToolResult = handler(request)
            return self._record_navigation(call, result, target)
        result = handler(request)
        if call.name == "close_package" and tool_succeeded(result):
            return with_state_update(
                result,
                complete_update(
                    call.state, {"active_strategy": "general", "general": initial_general_state()}
                ),
            )
        return result

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        call: ToolInvocation = ToolInvocation.from_request(request)
        if self._requires_page_confirmation(call):
            return reject(
                request,
                "page_confirmation_required",
                "Call confirm_page with your conclusion before another page operation.",
            )
        if call.name in NAVIGATION_TOOLS:
            target: PageTarget = self._resolve_navigation_target(call)
            result: ToolResult = await handler(request)
            return self._record_navigation(call, result, target)
        result = await handler(request)
        if call.name == "close_package" and tool_succeeded(result):
            return with_state_update(
                result,
                complete_update(
                    call.state, {"active_strategy": "general", "general": initial_general_state()}
                ),
            )
        return result

    def _requires_page_confirmation(self, call: ToolInvocation) -> bool:
        if unconfirmed_arrival(call.state["general"]) is None:
            return False
        if call.name in OBSERVATION_TOOLS or call.name == "confirm_page":
            return False
        return call.name in PAGE_OPERATIONS

    def _record_navigation(self, call: ToolInvocation, result: ToolResult, target: PageTarget) -> ToolResult:
        if not tool_succeeded(result):
            return result
        arrival: PageArrival = {
            "candidate_id": uuid4().hex,
            "path": target.path,
            "strategy": target.strategy,
            "app_root": target.app_root,
            "status": "awaiting_confirmation",
        }
        result = with_warning(
            result,
            call,
            "page_arrival_unconfirmed",
            f"Candidate {arrival['candidate_id']} targets {target.path}. Call confirm_page with your conclusion; query or screenshot only if needed.",
        )
        return with_state_update(
            result,
            complete_update(
                call.state,
                {"active_strategy": "general", "general": {"arrival": arrival, "app_root": target.app_root}},
            ),
        )

    def confirm_page(
        self,
        state: ConductorState,
        candidate_id: str,
        matches: bool,
        call_id: str,
    ) -> Command[str]:
        arrival: PageArrival | None = state["general"].get("arrival")
        validate_arrival(arrival, candidate_id)
        assert arrival is not None
        update: ConductorUpdate
        if matches:
            visit: PageVisit = {
                "visit_id": arrival["candidate_id"],
                "path": arrival["path"],
                "strategy": arrival["strategy"],
            }
            update = {
                "active_strategy": arrival["strategy"],
                "general": {
                    "active": True,
                    "visit": visit,
                    "app_root": arrival["app_root"],
                    "arrival": {**arrival, "status": "confirmed"},
                },
            }
        else:
            update = {
                "active_strategy": "general",
                "general": {"visit": None, "active": False, "arrival": None},
            }
        return Command(
            update={
                **complete_update(state, update),
                "messages": [
                    ToolMessage(
                        content="Page arrival confirmed."
                        if matches
                        else "Page mismatch. Pending confirmation cleared; no current page is confirmed. Inspect or navigate again.",
                        name="confirm_page",
                        tool_call_id=call_id,
                    )
                ],
            }
        )
