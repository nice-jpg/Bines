"""Adapt shared collection results to LangChain messages without exposing item data."""

import json
from collections.abc import Awaitable, Callable
from typing import Final

from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import BaseMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from src.agent_messages.text import message_content
from src.agent_messages.tool_results import ToolResult
from src.conductor.state import ConductorState, complete_update, current_visit, read_conductor_fields
from src.device import PageSnapshot, artifact_snapshot
from src.strategy.general.state import unconfirmed_arrival
from src.strategy.tool_io import ToolInvocation, command_messages, command_update
from src.tools.collection.service import CollectionService, CollectionStepResult, CompletionResult, StepStatus
from src.tools.shared.factory import create_tool

COLLECTION_XML_TOOL_NAMES: Final[set[str]] = {
    "tap_in_page",
    "swipe_up",
    "swipe_up_long",
    "swipe_down",
    "page_view",
}


class CollectionPersistenceMiddleware(AgentMiddleware[ConductorState]):
    """Keep item data private while reporting incomplete rows and collection health."""

    state_schema: type[ConductorState] = ConductorState

    def __init__(self, service: CollectionService) -> None:
        self.service: CollectionService = service

    def create_tools(self) -> list[StructuredTool]:
        return [create_tool(self.is_done)]

    def is_done(self, runtime: ToolRuntime[None, ConductorState]) -> Command[str]:
        """Check completion only on likely end-of-list evidence, not after ordinary swipes.

        Three consecutive complete, successfully persisted zero-new swipes are required.
        """
        completion: CompletionResult = self.service.completion(runtime.state)
        message: ToolMessage = ToolMessage(
            content=json.dumps(completion.payload, ensure_ascii=False),
            tool_call_id=runtime.tool_call_id,
            name="is_done",
            status="success" if completion.payload["ok"] else "error",
        )
        return Command(
            update={
                **complete_update(read_conductor_fields(runtime.state), completion.update),
                "messages": [message],
            }
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolResult],
    ) -> ToolResult:
        result: ToolResult = handler(request)
        if unconfirmed_arrival(ToolInvocation.from_request(request).state["general"]) is not None:
            return result
        return self._process_tool_result(request, result)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolResult]],
    ) -> ToolResult:
        result: ToolResult = await handler(request)
        if unconfirmed_arrival(ToolInvocation.from_request(request).state["general"]) is not None:
            return result
        return self._process_tool_result(request, result)

    def _process_tool_result(self, request: ToolCallRequest, result: ToolResult) -> ToolResult:
        if isinstance(result, Command):
            messages: list[object] = command_messages(result)
            for index, message in enumerate(messages):
                if isinstance(message, ToolMessage) and message.tool_call_id == request.tool_call["id"]:
                    processed: ToolResult = self._process_tool_result(request, message)
                    if isinstance(processed, Command):
                        update: dict[str, object] = {**command_update(result), **command_update(processed)}
                        update["messages"] = (
                            messages[:index] + command_messages(processed) + messages[index + 1 :]
                        )
                        return Command(
                            graph=result.graph, update=update, resume=result.resume, goto=result.goto
                        )
                    break
            return result
        invocation: ToolInvocation = ToolInvocation.from_request(request)
        tool_name: str = invocation.name
        if tool_name == "prepare_collector":
            return self._initialize_trained_merchant(request, result)
        if tool_name not in COLLECTION_XML_TOOL_NAMES:
            return result
        if result.status == "error":
            return result
        if not isinstance(message_content(result), str):
            return result
        state: ConductorState = invocation.state
        if tool_name == "tap_in_page" and invocation.args.get("purpose") == "dismiss_overlay":
            return result
        current_path: str = str(current_visit(state).get("path") or "")
        visit_id: str = str(current_visit(state).get("visit_id") or "")
        if not current_path or not self.service.training_registry.is_trained(visit_id, current_path):
            return result

        snapshot: PageSnapshot | None = artifact_snapshot(result.artifact)
        if not snapshot:
            return result

        step: CollectionStepResult = self.service.collect(state, snapshot, tool_name, invocation.args)
        advice_messages: list[BaseMessage] = [result]
        if step.status == StepStatus.FAILED:
            advice_messages.append(
                SystemMessage(
                    content=f"Collection health: {step.error}. Completion evidence is invalid; inspect the page and repair or retrain before continuing.",
                    additional_kwargs={
                        "bines_context": {
                            "kind": "collection_admission",
                            "page_visit_id": visit_id,
                            "source_tool_call_id": invocation.call_id,
                        }
                    },
                )
            )
        if step.status == StepStatus.INCOMPLETE:
            direction: str | None = step.update.get("merchant", {}).get("evidence", {}).get(
                "last_swipe"
            ) or state["merchant"].get("evidence", {}).get("last_swipe")
            advice: str = (
                f"Continue in the same direction with a short {direction} in the product region"
                if direction
                else "Confirm the intended product traversal direction and make a short swipe"
            )
            advice_messages.append(
                SystemMessage(
                    content=f"Some visible rows are incomplete. {advice} before checking completion.",
                    additional_kwargs={
                        "bines_context": {
                            "kind": "collection_admission",
                            "page_visit_id": visit_id,
                            "source_tool_call_id": invocation.call_id,
                        }
                    },
                )
            )
        return (
            Command(update={**complete_update(state, step.update), "messages": advice_messages})
            if step.update
            else result
        )

    def _initialize_trained_merchant(self, request: ToolCallRequest, result: ToolResult) -> ToolResult:
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result
        state: ConductorState = ToolInvocation.from_request(request).state
        if not self.service.training_registry.is_trained(
            str(current_visit(state).get("visit_id") or ""), str(current_visit(state).get("path") or "")
        ):
            return result
        step: CollectionStepResult = self.service.initialize(state)
        return Command(update={**complete_update(state, step.update), "messages": [result]})
