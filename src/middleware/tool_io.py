"""Typed boundary objects for LangChain tool middleware."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.messages.tool import ToolCall
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.types import Command

from src.agent_messages.text import message_content
from src.agent_messages.tool_results import ToolResult
from src.common.utils.json import JsonObject, JsonValue, json_object, load_json
from src.common.utils.validation import is_string_mapping, is_value_sequence
from src.conductor.state import ConductorState, read_conductor_state


def page_xml(result: ToolResult) -> str:
    """Read internal page evidence without parsing model-visible content."""
    messages: list[ToolMessage] = tool_messages(result)
    if not messages:
        return ""
    artifact: object = messages[-1].artifact
    if not is_string_mapping(artifact):
        return ""
    xml: object = artifact.get("page_xml")
    return xml if isinstance(xml, str) else ""


@dataclass(frozen=True)
class ToolInvocation:
    """Validated, immutable view of one LangChain tool-call request."""

    request: ToolCallRequest
    name: str
    call_id: str
    args: JsonObject
    state: ConductorState
    tool: BaseTool | None

    @classmethod
    def from_request(cls, request: ToolCallRequest) -> ToolInvocation:
        call: ToolCall = request.tool_call
        state: ConductorState = read_conductor_state(request.state)
        args: JsonObject = json_object(call.get("args", {}))
        tool: BaseTool | None = request.tool
        name: str = str(call.get("name") or (tool.name if tool is not None else ""))
        return cls(
            request=request,
            name=name,
            call_id=str(call.get("id") or ""),
            args=args,
            state=state,
            tool=tool,
        )

    @property
    def config(self) -> RunnableConfig:
        # ToolCallRequest erases ToolRuntime's state generic; config is independently typed.
        return self.request.runtime.config  # pyright: ignore[reportUnknownMemberType]

    @property
    def tool_metadata(self) -> Mapping[str, object] | None:
        if self.tool is None or self.tool.metadata is None:
            return None
        return self.tool.metadata


def tool_definition_name(tool: BaseTool | Mapping[str, object]) -> str:
    """Return a name from one model-visible LangChain tool definition."""

    if isinstance(tool, Mapping):
        function: object | None = tool.get("function")
        nested_name: object | None = function.get("name") if is_string_mapping(function) else None
        name: object = tool.get("name") or nested_name
        return name if isinstance(name, str) else ""
    return tool.name


def tool_messages(result: ToolResult) -> list[ToolMessage]:
    """Extract tool messages from a supported middleware result."""

    if isinstance(result, ToolMessage):
        return [result]
    if is_string_mapping(result.update):
        messages: object = result.update.get("messages")
        if is_value_sequence(messages):
            return [message for message in messages if isinstance(message, ToolMessage)]
    return []


def tool_succeeded(result: ToolResult) -> bool:
    """Evaluate the structured status of a tool result."""

    messages: list[ToolMessage] = tool_messages(result)
    if not messages:
        return False
    message: ToolMessage = messages[-1]
    if message.status == "error":
        return False
    content: object = message_content(message)
    if is_string_mapping(content):
        return not bool(content.get("error_type") or content.get("ok") is False)
    if isinstance(content, str) and content.lstrip().startswith("{"):
        try:
            payload: JsonValue = load_json(content)
        except json.JSONDecodeError:
            return True
        if isinstance(payload, dict):
            return not bool(payload.get("error_type") or payload.get("ok") is False)
    return True


def with_state_update(result: ToolResult, state_update: Mapping[str, object]) -> Command[str]:
    """Merge deterministic middleware state into a supported tool result."""

    if isinstance(result, ToolMessage):
        return Command(update={"messages": [result], **state_update})
    update: dict[str, object] = dict(result.update) if is_string_mapping(result.update) else {}
    update.update(state_update)
    return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)


def with_warning(result: ToolResult, invocation: ToolInvocation, code: str, text: str) -> Command[str]:
    """Append advisory context without replacing device evidence or tool status."""
    command: Command[str] = with_state_update(result, {})
    update: object = command.update
    raw: object = update.get("messages") if is_string_mapping(update) else None
    messages: list[object] = list(raw) if is_value_sequence(raw) else []
    messages.append(
        SystemMessage(
            content=f"Warning: {text}",
            additional_kwargs={
                "bines_context": {
                    "kind": "operation_warning",
                    "code": code,
                    "source_tool_call_id": invocation.call_id,
                }
            },
        )
    )
    return with_state_update(command, {"messages": messages})


def command_state_value(result: ToolResult, key: str) -> object:
    if isinstance(result, Command) and is_string_mapping(result.update):
        return result.update.get(key)
    return None


__all__: list[str] = [
    "ToolInvocation",
    "command_state_value",
    "tool_definition_name",
    "tool_messages",
    "tool_succeeded",
    "with_state_update",
]


def command_update(result: Command[str]) -> Mapping[str, object]:
    """Validate the mapping form of a framework command without discarding extension fields."""
    update: object = result.update
    return update if is_string_mapping(update) else {}


def command_messages(result: Command[str]) -> list[object]:
    """Preserve message objects while a middleware replaces one tool's result."""
    messages: object = command_update(result).get("messages")
    return list(messages) if is_value_sequence(messages) else []
