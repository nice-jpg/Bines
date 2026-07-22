"""Request-only conductor context projection by user and page blocks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
import json
from typing import Any

from langchain.agents.middleware.types import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage


MANUAL_CONTEXT_KIND = "page_manual"
MANUAL_PLACEHOLDER_CONTEXT_KIND = "page_manual_placeholder"
SHORT_LIVED_PAGE_TOOLS = frozenset(
    {
        "swipe_up",
        "swipe_up_long",
        "swipe_down",
        "swipe_to_random",
        "tap",
        "tap_in_page",
        "uiautomate",
        "screenshot",
    }
)
NAVIGATION_TOOLS = frozenset({"navigate_to", "navigate_back"})
RECENT_PAGE_TOOL_LIMIT = 3


class DeviceContextCompressionMiddleware(AgentMiddleware):
    """Project conductor history without mutating persisted graph messages."""

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(request.override(messages=compact_device_messages(request.messages)))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        projected = request.override(messages=compact_device_messages(request.messages))
        return await handler(projected)


@dataclass(frozen=True)
class ToolCallInfo:
    call_id: str
    name: str
    args: Mapping[str, Any]


@dataclass
class ManualFrame:
    path: str
    visit_id: str
    manual: Any
    body: list[Any] = field(default_factory=list)

    def without_tool_call(self, call_id: str) -> "ManualFrame":
        return ManualFrame(
            path=self.path,
            visit_id=self.visit_id,
            manual=self.manual,
            body=_remove_tool_calls(self.body, {call_id}) if call_id else list(self.body),
        )


@dataclass(frozen=True)
class NavigationTransition:
    call_id: str
    name: str
    next_path: str


def compact_device_messages(messages: Sequence[Any]) -> list[Any]:
    """Build the conductor model input from immutable conversation history."""

    prefix, user_blocks = _split_user_blocks(messages)
    projected = list(prefix)
    page_stack: list[ManualFrame] = []
    active_path = ""
    active_visit_id = ""
    active_manual: Any | None = None

    for block_index, block in enumerate(user_blocks):
        block_output, active = _project_user_block(
            block,
            block_index=block_index,
            active_path=active_path,
            active_visit_id=active_visit_id,
            active_manual=active_manual,
            page_stack=page_stack,
        )
        projected.extend(block_output)
        if active is not None:
            active_path = active.path
            active_visit_id = active.visit_id
            active_manual = active.manual

    if not user_blocks:
        return _limit_short_lived_tools(projected)
    return projected


def _split_user_blocks(messages: Sequence[Any]) -> tuple[list[Any], list[list[Any]]]:
    prefix: list[Any] = []
    blocks: list[list[Any]] = []
    current: list[Any] | None = None
    for message in messages:
        if _is_user_message(message):
            current = [message]
            blocks.append(current)
        elif current is None:
            prefix.append(message)
        else:
            current.append(message)
    return prefix, blocks


def _project_user_block(
    messages: Sequence[Any],
    *,
    block_index: int,
    active_path: str,
    active_visit_id: str,
    active_manual: Any | None,
    page_stack: list[ManualFrame],
) -> tuple[list[Any], ManualFrame | None]:
    if not messages:
        return [], None

    committed: list[Any] = [messages[0]]
    active = (
        ManualFrame(
            path=active_path,
            visit_id=active_visit_id,
            manual=_copy_manual_anchor(active_manual, block_index),
        )
        if active_manual is not None and active_path
        else None
    )
    placeholder_index = 0

    for message in messages[1:]:
        manual_identity = _manual_identity(message)
        if manual_identity is None:
            if active is None:
                committed.append(message)
            else:
                active.body.append(message)
            continue

        path, visit_id = manual_identity
        if active is None:
            active = ManualFrame(path=path, visit_id=visit_id, manual=message)
            continue
        if path == active.path and visit_id == active.visit_id:
            active.manual = message
            continue

        transition = _latest_navigation(active.body)
        committed.append(_manual_placeholder(active, block_index, placeholder_index))
        placeholder_index += 1
        stored = active.without_tool_call(transition.call_id) if transition is not None else active

        if transition is not None and transition.name == "navigate_to":
            page_stack.append(stored)
            active = ManualFrame(path=path, visit_id=visit_id, manual=message)
            continue
        if transition is not None and transition.name == "navigate_back":
            restored = _pop_page_frame(page_stack, transition.next_path or path)
            active = ManualFrame(
                path=path,
                visit_id=visit_id,
                manual=message,
                body=list(restored.body) if restored is not None else [],
            )
            continue

        page_stack.clear()
        active = ManualFrame(path=path, visit_id=visit_id, manual=message)

    if active is None:
        return _limit_short_lived_tools(committed), None
    return [*committed, active.manual, *_limit_short_lived_tools(active.body)], active


def _manual_identity(message: Any) -> tuple[str, str] | None:
    context = _message_context(message)
    if context.get("kind") != MANUAL_CONTEXT_KIND:
        return None
    path = str(context.get("canonical_path") or "").strip()
    if not path:
        return None
    return path, str(context.get("page_visit_id") or "")


def _message_context(message: Any) -> Mapping[str, Any]:
    if isinstance(message, BaseMessage):
        additional_kwargs = message.additional_kwargs
    elif isinstance(message, Mapping):
        additional_kwargs = message.get("additional_kwargs")
    else:
        return {}
    if not isinstance(additional_kwargs, Mapping):
        return {}
    context = additional_kwargs.get("bines_context")
    return context if isinstance(context, Mapping) else {}


def _copy_manual_anchor(message: Any, block_index: int) -> Any:
    identity = _manual_identity(message)
    visit_id = identity[1] if identity is not None else "current"
    anchor_id = f"page-manual-anchor:{block_index}:{visit_id or 'current'}"
    if isinstance(message, BaseMessage):
        return message.model_copy(update={"id": anchor_id})
    if isinstance(message, Mapping):
        copied = dict(message)
        copied["id"] = anchor_id
        return copied
    raise TypeError(f"Unsupported manual message type: {type(message).__name__}")


def _manual_placeholder(frame: ManualFrame, block_index: int, placeholder_index: int) -> SystemMessage:
    return SystemMessage(
        content=f"Entered {frame.path} and worked.",
        id=f"page-manual-placeholder:{block_index}:{placeholder_index}:{frame.visit_id or frame.path}",
        additional_kwargs={
            "bines_context": {
                "kind": MANUAL_PLACEHOLDER_CONTEXT_KIND,
                "canonical_path": frame.path,
                "page_visit_id": frame.visit_id,
            }
        },
    )


def _latest_navigation(messages: Sequence[Any]) -> NavigationTransition | None:
    latest: NavigationTransition | None = None
    for message in messages:
        for call in _tool_calls(message):
            if call.name not in NAVIGATION_TOOLS:
                continue
            latest = NavigationTransition(
                call_id=call.call_id,
                name=call.name,
                next_path=str(call.args.get("next_path") or ""),
            )
    return latest


def _pop_page_frame(page_stack: list[ManualFrame], target_path: str) -> ManualFrame | None:
    for index in range(len(page_stack) - 1, -1, -1):
        if page_stack[index].path != target_path:
            continue
        frame = page_stack[index]
        del page_stack[index:]
        return frame
    return None


def _limit_short_lived_tools(messages: Sequence[Any]) -> list[Any]:
    short_lived_ids = [
        call.call_id
        for message in messages
        for call in _tool_calls(message)
        if call.call_id and call.name in SHORT_LIVED_PAGE_TOOLS
    ]
    removed = set(short_lived_ids[:-RECENT_PAGE_TOOL_LIMIT])
    return _remove_tool_calls(messages, removed) if removed else list(messages)


def _remove_tool_calls(messages: Sequence[Any], removed_ids: set[str]) -> list[Any]:
    if not removed_ids:
        return list(messages)
    filtered: list[Any] = []
    for message in messages:
        tool_call_id = _tool_message_call_id(message)
        if tool_call_id in removed_ids:
            continue
        rewritten = _rewrite_assistant_tool_calls(message, removed_ids)
        if rewritten is not None:
            filtered.append(rewritten)
    return filtered


def _rewrite_assistant_tool_calls(message: Any, removed_ids: set[str]) -> Any | None:
    if isinstance(message, AIMessage):
        calls = list(message.tool_calls)
        raw_calls = _additional_raw_tool_calls(message.additional_kwargs)
        if not calls and not raw_calls:
            return message
        kept_calls = [call for call in calls if _raw_call_id(call) not in removed_ids]
        additional_kwargs = dict(message.additional_kwargs)
        if raw_calls:
            additional_kwargs["tool_calls"] = [
                call for call in raw_calls if _raw_call_id(call) not in removed_ids
            ]
        if not kept_calls and not additional_kwargs.get("tool_calls") and not _has_content(message.content):
            return None
        return message.model_copy(
            update={"tool_calls": kept_calls, "additional_kwargs": additional_kwargs}
        )

    if not isinstance(message, Mapping) or _message_role(message) != "assistant":
        return message
    calls = message.get("tool_calls")
    additional_kwargs = message.get("additional_kwargs")
    raw_calls = list(calls) if isinstance(calls, Sequence) and not isinstance(calls, (str, bytes)) else []
    nested_calls = _additional_raw_tool_calls(additional_kwargs)
    if not raw_calls and not nested_calls:
        return message
    rewritten = dict(message)
    if raw_calls:
        rewritten["tool_calls"] = [call for call in raw_calls if _raw_call_id(call) not in removed_ids]
    if isinstance(additional_kwargs, Mapping) and nested_calls:
        nested = dict(additional_kwargs)
        nested["tool_calls"] = [call for call in nested_calls if _raw_call_id(call) not in removed_ids]
        rewritten["additional_kwargs"] = nested
    remaining = rewritten.get("tool_calls") or _additional_raw_tool_calls(rewritten.get("additional_kwargs"))
    return rewritten if remaining or _has_content(rewritten.get("content")) else None


def _tool_calls(message: Any) -> list[ToolCallInfo]:
    if isinstance(message, AIMessage):
        raw_calls: Sequence[Any] = message.tool_calls or _additional_raw_tool_calls(message.additional_kwargs)
    elif isinstance(message, Mapping) and _message_role(message) == "assistant":
        direct = message.get("tool_calls")
        raw_calls = (
            direct
            if isinstance(direct, Sequence) and not isinstance(direct, (str, bytes)) and direct
            else _additional_raw_tool_calls(message.get("additional_kwargs"))
        )
    else:
        return []

    calls: list[ToolCallInfo] = []
    for raw_call in raw_calls:
        parsed = _parse_tool_call(raw_call)
        if parsed is not None:
            calls.append(parsed)
    return calls


def _parse_tool_call(raw_call: Any) -> ToolCallInfo | None:
    if not isinstance(raw_call, Mapping):
        return None
    call_id = str(raw_call.get("id") or raw_call.get("tool_call_id") or "")
    name = str(raw_call.get("name") or "")
    args: Any = raw_call.get("args") or {}
    function = raw_call.get("function")
    if isinstance(function, Mapping):
        name = name or str(function.get("name") or "")
        args = args or function.get("arguments") or {}
    if not call_id or not name:
        return None
    return ToolCallInfo(call_id=call_id, name=name, args=_normalize_args(args))


def _normalize_args(args: Any) -> Mapping[str, Any]:
    if isinstance(args, Mapping):
        return dict(args)
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, Mapping) else {}
    return {}


def _additional_raw_tool_calls(additional_kwargs: Any) -> list[Any]:
    if not isinstance(additional_kwargs, Mapping):
        return []
    calls = additional_kwargs.get("tool_calls")
    return list(calls) if isinstance(calls, Sequence) and not isinstance(calls, (str, bytes)) else []


def _raw_call_id(call: Any) -> str:
    return str(call.get("id") or call.get("tool_call_id") or "") if isinstance(call, Mapping) else ""


def _tool_message_call_id(message: Any) -> str:
    if isinstance(message, ToolMessage):
        return str(message.tool_call_id or "")
    if isinstance(message, Mapping) and _message_role(message) == "tool":
        return str(message.get("tool_call_id") or "")
    return ""


def _is_user_message(message: Any) -> bool:
    return isinstance(message, HumanMessage) or (
        isinstance(message, Mapping) and _message_role(message) in {"user", "human"}
    )


def _message_role(message: Mapping[str, Any]) -> str:
    return str(message.get("role") or message.get("type") or "").lower()


def _has_content(content: Any) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, Sequence):
        return bool(content)
    return content is not None


__all__ = [
    "DeviceContextCompressionMiddleware",
    "MANUAL_PLACEHOLDER_CONTEXT_KIND",
    "compact_device_messages",
]
