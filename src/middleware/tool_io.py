"""Typed boundary objects for LangChain tool middleware."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any, cast

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command


ToolResult = ToolMessage | Command


def page_xml(result: ToolResult) -> str:
    """Read internal page evidence without parsing model-visible content."""
    messages = tool_messages(result)
    if not messages:
        return ""
    artifact = messages[-1].artifact
    return str(artifact.get("page_xml") or "") if isinstance(artifact, Mapping) else ""


@dataclass(frozen=True)
class ToolInvocation:
    """Validated, immutable view of one LangChain tool-call request."""

    request: ToolCallRequest
    name: str
    call_id: str
    args: Mapping[str, Any]
    state: Mapping[str, Any]
    tool: BaseTool | None

    @classmethod
    def from_request(cls, request: ToolCallRequest) -> "ToolInvocation":
        call = request.tool_call
        state = request.state
        if not isinstance(state, Mapping):
            raise TypeError("ToolCallRequest.state must be a mapping.")
        args = call.get("args") or {}
        if not isinstance(args, Mapping):
            raise TypeError("Tool call args must be a mapping.")
        tool = request.tool
        name = str(call.get("name") or (tool.name if tool is not None else ""))
        return cls(
            request=request,
            name=name,
            call_id=str(call.get("id") or ""),
            args=args,
            state=state,
            tool=tool,
        )

    @property
    def tool_metadata(self) -> Mapping[str, Any] | None:
        if self.tool is None or self.tool.metadata is None:
            return None
        return self.tool.metadata


def tool_definition_name(tool: BaseTool | Mapping[str, Any]) -> str:
    """Return a name from one model-visible LangChain tool definition."""

    if isinstance(tool, Mapping):
        function = tool.get("function")
        nested_name = function.get("name") if isinstance(function, Mapping) else None
        return str(tool.get("name") or nested_name or "")
    return tool.name


def tool_messages(result: ToolResult) -> list[ToolMessage]:
    """Extract tool messages from a supported middleware result."""

    if isinstance(result, ToolMessage):
        return [result]
    if isinstance(result.update, Mapping):
        messages = result.update.get("messages") or []
        if isinstance(messages, Sequence):
            return [message for message in messages if isinstance(message, ToolMessage)]
    return []


def tool_succeeded(result: ToolResult) -> bool:
    """Evaluate the structured status of a tool result."""

    messages = tool_messages(result)
    if not messages:
        return False
    message = messages[-1]
    if message.status == "error":
        return False
    content = message.content
    if isinstance(content, Mapping):
        return not bool(content.get("error_type") or content.get("ok") is False)
    if isinstance(content, str) and content.lstrip().startswith("{"):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return True
        if isinstance(payload, Mapping):
            return not bool(payload.get("error_type") or payload.get("ok") is False)
    return True


def with_state_update(result: ToolResult, state_update: Mapping[str, Any]) -> Command:
    """Merge deterministic middleware state into a supported tool result."""

    if isinstance(result, ToolMessage):
        return Command(update={"messages": [result], **state_update})
    update = dict(result.update) if isinstance(result.update, Mapping) else {}
    update.update(state_update)
    return Command(graph=result.graph, update=update, resume=result.resume, goto=result.goto)


def command_state_value(result: ToolResult, key: str) -> Any | None:
    if isinstance(result, Command) and isinstance(result.update, Mapping):
        return result.update.get(key)
    return None


def require_tool_result(result: Any) -> ToolResult:
    """Reject unsupported middleware return types at the LangChain boundary."""

    if not isinstance(result, (ToolMessage, Command)):
        raise TypeError(f"Unsupported tool result: {type(result).__name__}")
    return cast(ToolResult, result)


__all__ = [
    "ToolInvocation",
    "ToolResult",
    "command_state_value",
    "require_tool_result",
    "tool_definition_name",
    "tool_messages",
    "tool_succeeded",
    "with_state_update",
]
