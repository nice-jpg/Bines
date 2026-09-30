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

try:
    from src.middleware.probe_state import COLLECTION_VISIT_SUMMARIES
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.probe_state import COLLECTION_VISIT_SUMMARIES


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
        "page_view",
        "page_catalog",
        "page_read",
        "page_node",
        "select_aaid",
        "update_ratio",
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
        summaries = _request_collection_summaries(request.state)
        return handler(
            request.override(
                messages=compact_device_messages(
                    request.messages,
                    collection_summaries=summaries,
                )
            )
        )

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        summaries = _request_collection_summaries(request.state)
        projected = request.override(
            messages=compact_device_messages(
                request.messages,
                collection_summaries=summaries,
            )
        )
        return await handler(projected)


@dataclass(frozen=True)
class ToolCallInfo:
    call_id: str
    name: str
    args: Mapping[str, Any]


@dataclass
class ManualOutputSlot:
    messages: list[Any]


@dataclass
class ManualFrame:
    path: str
    visit_id: str
    manual: Any
    body: list[Any] = field(default_factory=list)
    output_slot: ManualOutputSlot | None = None
    inherited_output_slot: bool = False

    def without_tool_call(self, call_id: str) -> "ManualFrame":
        return ManualFrame(
            path=self.path,
            visit_id=self.visit_id,
            manual=self.manual,
            body=_remove_tool_call_turns(self.body, {call_id}) if call_id else list(self.body),
            output_slot=self.output_slot,
            inherited_output_slot=self.inherited_output_slot,
        )


@dataclass(frozen=True)
class NavigationTransition:
    call_id: str
    name: str
    next_path: str


def compact_device_messages(
    messages: Sequence[Any],
    *,
    collection_summaries: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[Any]:
    """Build the conductor model input from immutable conversation history."""

    prefix, user_blocks = _split_user_blocks(messages)
    projected = list(prefix)
    page_stack: list[ManualFrame] = []
    active_path = ""
    active_visit_id = ""
    active_manual: Any | None = None
    active_output_slot: ManualOutputSlot | None = None

    for block_index, block in enumerate(user_blocks):
        block_output, active = _project_user_block(
            block,
            block_index=block_index,
            active_path=active_path,
            active_visit_id=active_visit_id,
            active_manual=active_manual,
            active_output_slot=active_output_slot,
            page_stack=page_stack,
            collection_summaries=collection_summaries or {},
        )
        projected.extend(block_output)
        if active is not None:
            active_path = active.path
            active_visit_id = active.visit_id
            active_manual = active.manual
            active_output_slot = active.output_slot

    if not user_blocks:
        return _limit_short_lived_tools(projected)
    return _expand_manual_slots(projected)


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
    active_output_slot: ManualOutputSlot | None,
    page_stack: list[ManualFrame],
    collection_summaries: Mapping[str, Mapping[str, Any]],
) -> tuple[list[Any], ManualFrame | None]:
    if not messages:
        return [], None

    committed: list[Any] = [messages[0]]
    active = (
        ManualFrame(
            path=active_path,
            visit_id=active_visit_id,
            manual=_copy_manual_anchor(active_manual, block_index),
            output_slot=active_output_slot,
            inherited_output_slot=active_output_slot is not None,
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
        placeholder = _manual_placeholder(
            active,
            block_index,
            placeholder_index,
            collection_summaries,
        )
        output_slot = _collapse_manual_frame(
            committed,
            active,
            placeholder,
        )
        placeholder_index += 1
        stored = active.without_tool_call(transition.call_id) if transition is not None else active
        stored.output_slot = output_slot
        stored.inherited_output_slot = False

        if transition is not None and transition.name == "navigate_to":
            page_stack.append(stored)
            active = ManualFrame(path=path, visit_id=visit_id, manual=message)
            continue
        if transition is not None and transition.name == "navigate_back":
            restored = _pop_page_frame(page_stack, transition.next_path or path)
            if restored is not None and restored.output_slot is not None:
                restored.output_slot.messages = [
                    restored.manual, *_limit_short_lived_tools(restored.body)
                ]
            active = ManualFrame(
                path=path,
                visit_id=visit_id,
                manual=message,
            )
            continue

        page_stack.clear()
        active = ManualFrame(path=path, visit_id=visit_id, manual=message)

    if active is None:
        return _limit_short_lived_tools(committed), None
    active_messages = [active.manual, *_limit_short_lived_tools(active.body)]
    if active.output_slot is not None and not active.inherited_output_slot:
        active.output_slot.messages = active_messages
        return committed, active
    active.output_slot = ManualOutputSlot(messages=active_messages)
    active.inherited_output_slot = False
    committed.append(active.output_slot)
    return committed, active


def _collapse_manual_frame(
    committed: list[Any],
    frame: ManualFrame,
    placeholder: SystemMessage,
) -> ManualOutputSlot:
    if frame.output_slot is not None and not frame.inherited_output_slot:
        frame.output_slot.messages = [placeholder]
        return frame.output_slot
    output_slot = ManualOutputSlot(messages=[placeholder])
    committed.append(output_slot)
    return output_slot


def _expand_manual_slots(messages: Sequence[Any]) -> list[Any]:
    expanded: list[Any] = []
    for message in messages:
        if isinstance(message, ManualOutputSlot):
            expanded.extend(message.messages)
        else:
            expanded.append(message)
    return expanded


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


def _manual_placeholder(
    frame: ManualFrame,
    block_index: int,
    placeholder_index: int,
    collection_summaries: Mapping[str, Mapping[str, Any]],
) -> SystemMessage:
    summary = collection_summaries.get(frame.visit_id, {})
    display_path = _placeholder_path(frame.path, summary)
    collected_items = _non_negative_int(summary.get("collected_items"))
    return SystemMessage(
        content=_page_activity_description(frame, display_path, summary),
        id=f"page-manual-placeholder:{block_index}:{placeholder_index}:{frame.visit_id or frame.path}",
        additional_kwargs={
            "bines_context": {
                "kind": MANUAL_PLACEHOLDER_CONTEXT_KIND,
                "canonical_path": frame.path,
                "page_visit_id": frame.visit_id,
                "display_path": display_path,
                "collected_items": collected_items,
            }
        },
    )


def _page_activity_description(
    frame: ManualFrame, display_path: str, summary: Mapping[str, Any]
) -> str:
    """Describe recorded action intents without inventing successful outcomes."""
    intents: list[str] = []
    for message in frame.body:
        for call in _tool_calls(message):
            if call.name in NAVIGATION_TOOLS:
                target = str(call.args.get("next_path") or "")
                intent = f"requested {'return to' if call.name == 'navigate_back' else 'navigation to'} {target}"
                merchant_name = call.args.get("merchant_name")
                if merchant_name:
                    intent += f" for merchant {merchant_name}"
            elif call.name == "tap_in_page":
                intent = "attempted overlay dismissal" if call.args.get("purpose") == "dismiss_overlay" else "interacted with page controls"
            elif call.name.startswith("swipe_"):
                intent = f"scrolled using {call.name}"
            elif call.name == "prepare_collector":
                intent = "requested collection-script validation"
            elif call.name == "is_done":
                intent = "checked collection completion"
            elif call.name == "screenshot":
                intent = "inspected a screenshot"
            elif call.name in SHORT_LIVED_PAGE_TOOLS:
                intent = "queried the page view"
            else:
                intent = f"called {call.name}"
            if intent not in intents:
                intents.append(intent)
    activity = "; ".join(intents) if intents else "read the page manual"
    if "collected_items" in summary:
        activity += f"; archived {_non_negative_int(summary['collected_items'])} items"
    return f"At {display_path}: {activity}."


def _request_collection_summaries(state: Mapping[str, Any]) -> Mapping[str, Mapping[str, Any]]:
    summaries = state.get(COLLECTION_VISIT_SUMMARIES)
    if not isinstance(summaries, Mapping):
        return {}
    return {
        str(visit_id): summary
        for visit_id, summary in summaries.items()
        if isinstance(summary, Mapping)
    }


def _placeholder_path(canonical_path: str, summary: Mapping[str, Any]) -> str:
    parts = [part for part in canonical_path.strip("/").split("/") if part]
    profile = summary.get("merchant_profile")
    merchant_name = ""
    if summary.get("is_leaf") is True and isinstance(profile, Mapping):
        merchant_name = _path_segment(profile.get("name"))
    if merchant_name and parts:
        parts[-1] = merchant_name
    return "/" + "/".join(parts)


def _path_segment(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().replace("/", "-").replace("\\", "-")


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


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
    return _remove_tool_call_turns(messages, removed) if removed else list(messages)


def _remove_tool_call_turns(messages: Sequence[Any], removed_ids: set[str]) -> list[Any]:
    if not removed_ids:
        return list(messages)
    removed_turn_ids: set[str] = set()
    for message in messages:
        calls = _tool_calls(message)
        if any(call.call_id in removed_ids for call in calls):
            removed_turn_ids.update(call.call_id for call in calls if call.call_id)
    filtered: list[Any] = []
    for message in messages:
        if any(call.call_id in removed_turn_ids for call in _tool_calls(message)):
            continue
        tool_call_id = _tool_message_call_id(message)
        if tool_call_id in removed_turn_ids:
            continue
        filtered.append(message)
    return filtered


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


__all__ = [
    "DeviceContextCompressionMiddleware",
    "MANUAL_PLACEHOLDER_CONTEXT_KIND",
    "compact_device_messages",
]
