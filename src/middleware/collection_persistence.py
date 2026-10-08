"""Adapt shared collection results to LangChain messages without exposing item data."""

from collections.abc import Awaitable, Callable, Mapping
from typing import Any
import json
from langchain.agents.middleware.types import AgentMiddleware, ToolCallRequest
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command
from src.collection.service import CollectionService, StepStatus
from src.probe_state import ProbeState, COLLECTION_LAST_SWIPE
from src.middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
from src.middleware.tool_io import ToolInvocation

COLLECTION_XML_TOOL_NAMES = {"tap_in_page", "swipe_up", "swipe_up_long", "swipe_down", "page_view"}


class CollectionPersistenceMiddleware(AgentMiddleware[ProbeState]):
    """Keep manual tool collection silent, except for incomplete-row advice."""
    state_schema = ProbeState

    def __init__(self, service: CollectionService):
        self.service = service

    def create_tools(self) -> list[StructuredTool]:
        return [StructuredTool.from_function(self.is_done)]

    def is_done(self, runtime: ToolRuntime) -> Command:
        """Check completion only on likely end-of-list evidence, not after ordinary swipes.

        Three consecutive complete, successfully persisted zero-new swipes are required.
        """
        completion = self.service.completion(runtime.state)
        message = ToolMessage(
            content=json.dumps(completion.payload, ensure_ascii=False),
            tool_call_id=runtime.tool_call_id, name="is_done",
            status="success" if completion.payload["ok"] else "error",
        )
        return Command(update={**completion.update, "messages": [message]})

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[Any], ToolMessage | Any],
    ) -> ToolMessage | Any:
        result = handler(request)
        return self._process_tool_result(request, result)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[Any], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        result = await handler(request)
        return self._process_tool_result(request, result)

    def _process_tool_result(self, request: ToolCallRequest, result: ToolMessage | Any) -> ToolMessage | Any:
        if isinstance(result, Command) and isinstance(result.update, Mapping):
            messages = list(result.update.get("messages") or [])
            for index, message in enumerate(messages):
                if isinstance(message, ToolMessage) and message.tool_call_id == request.tool_call["id"]:
                    processed = self._process_tool_result(request, message)
                    if isinstance(processed, Command):
                        update = {**result.update, **processed.update}
                        update["messages"] = messages[:index] + list(processed.update.get("messages") or []) + messages[index + 1:]
                        return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)
                    break
            return result
        invocation = ToolInvocation.from_request(request)
        tool_name = invocation.name
        if tool_name == "prepare_collector":
            return self._initialize_trained_merchant(request, result)
        if tool_name not in COLLECTION_XML_TOOL_NAMES:
            return result
        if not isinstance(result, ToolMessage):
            return result
        if result.status == "error":
            return result
        if not isinstance(result.content, str):
            return result
        state = invocation.state
        if tool_name == "tap_in_page" and invocation.args.get("purpose") == "dismiss_overlay":
            return result
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        visit_id = str(state.get(PAGE_VISIT_ID) or "")
        if not current_path or not self.service.training_registry.is_trained(visit_id, current_path):
            return result

        snapshot = result.artifact.get("page_snapshot") if isinstance(result.artifact, Mapping) else None
        if not snapshot:
            return result

        step = self.service.collect(state, snapshot, tool_name, invocation.args)
        messages = [result]
        if step.status == StepStatus.INCOMPLETE:
            direction = step.update.get(COLLECTION_LAST_SWIPE) or state.get(COLLECTION_LAST_SWIPE)
            advice = (f"Continue in the same direction with a short {direction} in the product region"
                      if direction else "Confirm the intended product traversal direction and make a short swipe")
            messages.append(SystemMessage(
                content=f"Some visible rows are incomplete. {advice} before checking completion.",
                additional_kwargs={"bines_context": {"kind": "collection_admission", "page_visit_id": visit_id,
                                                     "source_tool_call_id": invocation.call_id}},
            ))
        return Command(update={**step.update, "messages": messages}) if step.update else result

    def _initialize_trained_merchant(self, request: ToolCallRequest, result):
        if not isinstance(result, ToolMessage) or result.status == "error":
            return result
        state = request.state
        if not self.service.training_registry.is_trained(
            str(state.get(PAGE_VISIT_ID) or ""), str(state.get(PAGE_CONTEXT_PATH) or "")
        ):
            return result
        step = self.service.initialize(state)
        return Command(update={**step.update, "messages": [result]})
