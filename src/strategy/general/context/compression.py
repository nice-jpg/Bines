"""Request-only conductor context projection by user and page blocks."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, overload

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from src.agent_messages.text import message_additional_kwargs
from src.agent_messages.types import HistoryMessage, SerializedHistoryMessage, SerializedToolCall
from src.common.utils.json import JsonObject, JsonValue, json_object, load_json
from src.common.utils.validation import is_string_mapping, is_value_sequence
from src.conductor.state import CollectionVisitSummary, read_conductor_fields
from src.strategy.general.context.messages import (
    MANUAL_CONTEXT_KIND,
    MANUAL_PLACEHOLDER_CONTEXT_KIND,
    PAGE_MESSAGE_CONTEXT_KEY,
    PageMessageContext,
    read_page_message_context,
)

SHORT_LIVED_PAGE_TOOLS: Final[frozenset[str]] = frozenset(
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
        "page_nodes_at",
        "select_aaid",
        "node_update_ratio",
        "view_update_ratio",
        "screenshot",
    }
)
NAVIGATION_TOOLS: Final[frozenset[str]] = frozenset({"navigate_to", "navigate_back"})
RECENT_PAGE_TOOL_LIMIT: Final[int] = 3
RECENT_USER_BLOCK_LIMIT: Final[int] = 3


class DeviceContextCompressionMiddleware(AgentMiddleware[AgentState, None]):
    """Project conductor history without mutating persisted graph messages."""

    def wrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], ModelResponse[T]],
    ) -> ModelResponse[T]:
        summaries: Mapping[str, CollectionVisitSummary] = _request_collection_summaries(request.state)
        return handler(
            request.override(
                messages=compact_device_messages(
                    request.messages,
                    collection_summaries=summaries,
                )
            )
        )

    async def awrap_model_call[T](
        self,
        request: ModelRequest[None],
        handler: Callable[[ModelRequest[None]], Awaitable[ModelResponse[T]]],
    ) -> ModelResponse[T]:
        summaries: Mapping[str, CollectionVisitSummary] = _request_collection_summaries(request.state)
        projected: ModelRequest[None] = request.override(
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
    args: JsonObject


@dataclass
class ManualOutputSlot:
    messages: list[HistoryMessage]


@dataclass
class ManualFrame:
    path: str
    visit_id: str
    manual: HistoryMessage
    body: list[HistoryMessage] = field(default_factory=list[HistoryMessage])
    output_slot: ManualOutputSlot | None = None
    inherited_output_slot: bool = False

    def without_tool_call(self, call_id: str) -> ManualFrame:
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


@overload
def compact_device_messages(
    messages: Sequence[AnyMessage],
    *,
    collection_summaries: Mapping[str, CollectionVisitSummary] | None = None,
) -> list[AnyMessage]: ...


@overload
def compact_device_messages(
    messages: Sequence[HistoryMessage],
    *,
    collection_summaries: Mapping[str, CollectionVisitSummary] | None = None,
) -> Sequence[HistoryMessage]: ...


def compact_device_messages(
    messages: Sequence[HistoryMessage],
    *,
    collection_summaries: Mapping[str, CollectionVisitSummary] | None = None,
) -> Sequence[HistoryMessage]:
    """Build the conductor model input from immutable conversation history."""

    prefix, user_blocks = _split_user_blocks(messages)
    projected: list[HistoryMessage | ManualOutputSlot] = list(prefix)
    page_stack: list[ManualFrame] = []
    active_path: str = ""
    active_visit_id: str = ""
    active_manual: HistoryMessage | None = None
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
        if block_index < len(user_blocks) - RECENT_USER_BLOCK_LIMIT:
            projected.extend([block[0], _historical_block_record(block_output, block_index)])
        else:
            projected.extend(block_output)
        if active is not None:
            active_path = active.path
            active_visit_id = active.visit_id
            active_manual = active.manual
            active_output_slot = active.output_slot

    if not user_blocks:
        return _limit_short_lived_tools(_expand_manual_slots(projected))
    result = _expand_manual_slots(projected)
    # The latest response may still require its tool receipts on the server.
    latest_call = next((message for message in reversed(user_blocks[-1]) if (
        isinstance(message, AIMessage) or isinstance(message, Mapping) and _message_role(message) == "assistant"
    )), None)
    if latest_call is not None:
        calls = {call.call_id for call in _tool_calls(latest_call)}
        retained = {call.call_id for message in result for call in _tool_calls(message)}
        if not calls & retained:
            result.append(latest_call)
            result.extend(message for message in messages if _tool_message_call_id(message) in calls)
    return result


def _historical_block_record(
    messages: Sequence[HistoryMessage | ManualOutputSlot], block_index: int
) -> SystemMessage:
    """Retain action/outcome records without old manuals, images, or tool payloads."""
    records: list[str] = []
    counts: dict[str, int] = {}
    for message in _expand_manual_slots(messages):
        identity = _manual_identity(message)
        if identity is not None:
            records.append(f"Worked at {identity[0]}.")
        context = read_page_message_context(message)
        if context is not None and context.get("kind") == MANUAL_PLACEHOLDER_CONTEXT_KIND:
            content = message.content if isinstance(message, BaseMessage) else message.get("content", "")
            if isinstance(content, str):
                records.append(content)
        calls = _tool_calls(message)
        for call in calls:
            counts[call.name] = counts.get(call.name, 0) + 1
        is_ai = isinstance(message, AIMessage) or (
            isinstance(message, Mapping) and _message_role(message) == "assistant"
        )
        if is_ai and not calls:
            content = message.content if isinstance(message, BaseMessage) else message.get("content", "")
            if isinstance(content, str) and content.strip():
                records.append(f"Agent response: {content}")
        if _tool_message_call_id(message):
            status = message.status if isinstance(message, ToolMessage) else (
                message.get("status", "success") if isinstance(message, Mapping) else "success"
            )
            if status == "error":
                records.append("A tool call returned an error.")
    if counts:
        records.append("Tool calls: " + ", ".join(f"{name} x{count}" for name, count in counts.items()) + ".")
    return SystemMessage(
        content="Processing record:\n" + "\n".join(dict.fromkeys(records)),
        id=f"user-block-record:{block_index}",
    )


def _split_user_blocks(
    messages: Sequence[HistoryMessage],
) -> tuple[list[HistoryMessage], list[list[HistoryMessage]]]:
    prefix: list[HistoryMessage] = []
    blocks: list[list[HistoryMessage]] = []
    current: list[HistoryMessage] | None = None
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
    messages: Sequence[HistoryMessage],
    *,
    block_index: int,
    active_path: str,
    active_visit_id: str,
    active_manual: HistoryMessage | None,
    active_output_slot: ManualOutputSlot | None,
    page_stack: list[ManualFrame],
    collection_summaries: Mapping[str, CollectionVisitSummary],
) -> tuple[list[HistoryMessage | ManualOutputSlot], ManualFrame | None]:
    if not messages:
        return [], None

    committed: list[HistoryMessage | ManualOutputSlot] = [messages[0]]
    active: ManualFrame | None = (
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
    placeholder_index: int = 0

    for message in messages[1:]:
        manual_identity: tuple[str, str] | None = _manual_identity(message)
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

        transition: NavigationTransition | None = _latest_navigation(active.body)
        placeholder: SystemMessage = _manual_placeholder(
            active,
            block_index,
            placeholder_index,
            collection_summaries,
        )
        output_slot: ManualOutputSlot = _collapse_manual_frame(
            committed,
            active,
            placeholder,
        )
        placeholder_index += 1
        stored: ManualFrame = (
            active.without_tool_call(transition.call_id) if transition is not None else active
        )
        stored.output_slot = output_slot
        stored.inherited_output_slot = False

        if transition is not None and transition.name == "navigate_to":
            page_stack.append(stored)
            active = ManualFrame(path=path, visit_id=visit_id, manual=message)
            continue
        if transition is not None and transition.name == "navigate_back":
            restored: ManualFrame | None = _pop_page_frame(page_stack, transition.next_path or path)
            if restored is not None and restored.output_slot is not None:
                restored.output_slot.messages = [restored.manual, *_limit_short_lived_tools(restored.body)]
            active = ManualFrame(
                path=path,
                visit_id=visit_id,
                manual=message,
            )
            continue

        page_stack.clear()
        active = ManualFrame(path=path, visit_id=visit_id, manual=message)

    if active is None:
        return [*_limit_short_lived_tools(_expand_manual_slots(committed))], None
    active_messages: list[HistoryMessage] = [active.manual, *_limit_short_lived_tools(active.body)]
    if active.output_slot is not None and not active.inherited_output_slot:
        active.output_slot.messages = active_messages
        return committed, active
    active.output_slot = ManualOutputSlot(messages=active_messages)
    active.inherited_output_slot = False
    committed.append(active.output_slot)
    return committed, active


def _collapse_manual_frame(
    committed: list[HistoryMessage | ManualOutputSlot],
    frame: ManualFrame,
    placeholder: SystemMessage,
) -> ManualOutputSlot:
    if frame.output_slot is not None and not frame.inherited_output_slot:
        frame.output_slot.messages = [placeholder]
        return frame.output_slot
    output_slot: ManualOutputSlot = ManualOutputSlot(messages=[placeholder])
    committed.append(output_slot)
    return output_slot


def _expand_manual_slots(messages: Sequence[HistoryMessage | ManualOutputSlot]) -> list[HistoryMessage]:
    expanded: list[HistoryMessage] = []
    for message in messages:
        if isinstance(message, ManualOutputSlot):
            expanded.extend(message.messages)
        else:
            expanded.append(message)
    return expanded


def _manual_identity(message: HistoryMessage) -> tuple[str, str] | None:
    context: PageMessageContext | None = read_page_message_context(message)
    if context is None or context.get("kind") != MANUAL_CONTEXT_KIND:
        return None
    path: str = str(context.get("canonical_path") or "").strip()
    if not path:
        return None
    return path, str(context.get("page_visit_id") or "")


def _copy_manual_anchor(message: HistoryMessage, block_index: int) -> HistoryMessage:
    identity: tuple[str, str] | None = _manual_identity(message)
    visit_id: str = identity[1] if identity is not None else "current"
    anchor_id: str = f"page-manual-anchor:{block_index}:{visit_id or 'current'}"
    if isinstance(message, BaseMessage):
        return message.model_copy(update={"id": anchor_id})
    copied: SerializedHistoryMessage = message.copy()
    copied["id"] = anchor_id
    return copied


def _manual_placeholder(
    frame: ManualFrame,
    block_index: int,
    placeholder_index: int,
    collection_summaries: Mapping[str, CollectionVisitSummary],
) -> SystemMessage:
    summary: CollectionVisitSummary | None = collection_summaries.get(frame.visit_id)
    display_path: str = _placeholder_path(frame.path, summary)
    collected_items: int = _non_negative_int(summary["collected_items"] if summary is not None else None)
    return SystemMessage(
        content=_page_activity_description(frame, display_path, summary),
        id=f"page-manual-placeholder:{block_index}:{placeholder_index}:{frame.visit_id or frame.path}",
        additional_kwargs={
            PAGE_MESSAGE_CONTEXT_KEY: {
                "kind": MANUAL_PLACEHOLDER_CONTEXT_KIND,
                "canonical_path": frame.path,
                "page_visit_id": frame.visit_id,
                "display_path": display_path,
                "collected_items": collected_items,
            }
        },
    )


def _page_activity_description(
    frame: ManualFrame, display_path: str, summary: CollectionVisitSummary | None
) -> str:
    """Describe recorded action intents without inventing successful outcomes."""
    intents: list[str] = []
    for message in frame.body:
        for call in _tool_calls(message):
            if call.name in NAVIGATION_TOOLS:
                target: str = _optional_text(call.args.get("next_path"))
                intent: str = (
                    f"requested {'return to' if call.name == 'navigate_back' else 'navigation to'} {target}"
                )
                merchant_name: str = _optional_text(call.args.get("merchant_name"))
                if merchant_name:
                    intent += f" for merchant {merchant_name}"
            elif call.name == "tap_in_page":
                intent = (
                    "attempted overlay dismissal"
                    if call.args.get("purpose") == "dismiss_overlay"
                    else "interacted with page controls"
                )
            elif call.name.startswith("swipe_"):
                intent = f"scrolled using {call.name}"
            elif call.name == "spawn_subagent" and call.args.get("agent_type") == "collector":
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
    activity: str = "; ".join(intents) if intents else "read the page manual"
    if summary is not None:
        activity += f"; archived {_non_negative_int(summary['collected_items'])} items"
    return f"At {display_path}: {activity}."


def _request_collection_summaries(state: object) -> Mapping[str, CollectionVisitSummary]:
    return read_conductor_fields(state)["probe"].get("visit_summaries", {})


def _placeholder_path(canonical_path: str, summary: CollectionVisitSummary | None) -> str:
    parts: list[str] = [part for part in canonical_path.strip("/").split("/") if part]
    profile: JsonObject | None = summary["merchant_profile"] if summary is not None else None
    merchant_name: str = ""
    if summary is not None and summary["is_leaf"] is True and profile is not None:
        merchant_name = _path_segment(profile.get("name"))
    if merchant_name and parts:
        parts[-1] = merchant_name
    return "/" + "/".join(parts)


def _path_segment(value: JsonValue) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().replace("/", "-").replace("\\", "-")


def _non_negative_int(value: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)


def _latest_navigation(messages: Sequence[HistoryMessage]) -> NavigationTransition | None:
    latest: NavigationTransition | None = None
    for message in messages:
        for call in _tool_calls(message):
            if call.name not in NAVIGATION_TOOLS:
                continue
            latest = NavigationTransition(
                call_id=call.call_id,
                name=call.name,
                next_path=_optional_text(call.args.get("next_path")),
            )
    return latest


def _pop_page_frame(page_stack: list[ManualFrame], target_path: str) -> ManualFrame | None:
    for index in range(len(page_stack) - 1, -1, -1):
        if page_stack[index].path != target_path:
            continue
        frame: ManualFrame = page_stack[index]
        del page_stack[index:]
        return frame
    return None


def _limit_short_lived_tools(messages: Sequence[HistoryMessage]) -> list[HistoryMessage]:
    short_lived_ids: list[str] = [
        call.call_id
        for message in messages
        for call in _tool_calls(message)
        if call.call_id and call.name in SHORT_LIVED_PAGE_TOOLS
    ]
    removed: set[str] = set(short_lived_ids[:-RECENT_PAGE_TOOL_LIMIT])
    return _remove_tool_call_turns(messages, removed) if removed else list(messages)


def _remove_tool_call_turns(
    messages: Sequence[HistoryMessage], removed_ids: set[str]
) -> list[HistoryMessage]:
    if not removed_ids:
        return list(messages)
    removed_turn_ids: set[str] = set()
    for message in messages:
        calls: list[ToolCallInfo] = _tool_calls(message)
        if any(call.call_id in removed_ids for call in calls):
            removed_turn_ids.update(call.call_id for call in calls if call.call_id)
    filtered: list[HistoryMessage] = []
    for message in messages:
        if any(call.call_id in removed_turn_ids for call in _tool_calls(message)):
            continue
        tool_call_id: str = _tool_message_call_id(message)
        if tool_call_id in removed_turn_ids:
            continue
        filtered.append(message)
    return filtered


def _tool_calls(message: HistoryMessage) -> list[ToolCallInfo]:
    if isinstance(message, AIMessage):
        raw_calls: Sequence[object] = message.tool_calls or _additional_raw_tool_calls(
            message_additional_kwargs(message)
        )
    elif isinstance(message, Mapping) and _message_role(message) == "assistant":
        direct: list[SerializedToolCall] | None = message.get("tool_calls")
        raw_calls = (
            direct
            if isinstance(direct, Sequence) and not isinstance(direct, str | bytes) and direct
            else _additional_raw_tool_calls(message.get("additional_kwargs"))
        )
    else:
        return []

    calls: list[ToolCallInfo] = []
    for raw_call in raw_calls:
        parsed: ToolCallInfo | None = _parse_tool_call(raw_call)
        if parsed is not None:
            calls.append(parsed)
    return calls


def _parse_tool_call(raw_call: object) -> ToolCallInfo | None:
    if not is_string_mapping(raw_call):
        return None
    call_id: str = _optional_text(raw_call.get("id")) or _optional_text(raw_call.get("tool_call_id"))
    name: str = _optional_text(raw_call.get("name"))
    args: object = raw_call.get("args") or {}
    function: object = raw_call.get("function")
    if is_string_mapping(function):
        name = name or _optional_text(function.get("name"))
        args = args or function.get("arguments") or {}
    if not call_id or not name:
        return None
    return ToolCallInfo(call_id=call_id, name=name, args=_normalize_args(args))


def _normalize_args(args: object) -> JsonObject:
    if is_string_mapping(args):
        return json_object(args)
    if isinstance(args, str):
        try:
            parsed: JsonValue = load_json(args)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _additional_raw_tool_calls(additional_kwargs: object) -> list[object]:
    if not is_string_mapping(additional_kwargs):
        return []
    calls: object = additional_kwargs.get("tool_calls")
    return list(calls) if is_value_sequence(calls) else []


def _tool_message_call_id(message: HistoryMessage) -> str:
    if isinstance(message, ToolMessage):
        return str(message.tool_call_id or "")
    if isinstance(message, Mapping) and _message_role(message) == "tool":
        return str(message.get("tool_call_id") or "")
    return ""


def _is_user_message(message: HistoryMessage) -> bool:
    return isinstance(message, HumanMessage) or (
        isinstance(message, Mapping) and _message_role(message) in {"user", "human"}
    )


def _message_role(message: SerializedHistoryMessage) -> str:
    return str(message.get("role") or message.get("type") or "").lower()


__all__: list[str] = [
    "DeviceContextCompressionMiddleware",
    "MANUAL_PLACEHOLDER_CONTEXT_KIND",
    "compact_device_messages",
]


def _optional_text(value: object) -> str:
    return value if isinstance(value, str) else ""
