"""Device context compression middleware for LangChain agents."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

XML_DEVICE_TOOLS = {
    "uiautomate",
    "tap_in_page",
    "navigate_to",
    "navigate_back",
    "swipe_up",
    "swipe_down",
}
BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


class DeviceContextCompressionMiddleware(AgentMiddleware):
    """Keep only the newest device XML result and summarize older ones."""

    def before_model(self, state: Mapping[str, Any], runtime: Any | None = None) -> dict[str, Any] | None:
        messages = state.get("messages") if isinstance(state, Mapping) else None
        if not messages:
            return None
        compacted = compact_device_messages(messages)
        return {"messages": compacted}


@dataclass(frozen=True)
class ToolCallInfo:
    name: str | None
    args: Mapping[str, Any]


@dataclass(frozen=True)
class XmlToolResult:
    index: int
    tool_name: str
    args: Mapping[str, Any]
    prior_xml: str | None


def compact_device_messages(messages: Sequence[Any]) -> list[Any]:
    """Replace stale device XML tool results with concise action summaries."""

    tool_calls: dict[str, ToolCallInfo] = {}
    xml_results: list[XmlToolResult] = []
    latest_xml: str | None = None

    for index, message in enumerate(messages):
        tool_calls.update(_extract_tool_calls(message))
        tool_call_id = _tool_call_id(message)
        tool_info = tool_calls.get(tool_call_id or "")
        tool_name = _tool_name(message) or (tool_info.name if tool_info else None)
        content = _message_content(message)
        xml_content = _xml_content(content)

        if not tool_name or tool_name not in XML_DEVICE_TOOLS or xml_content is None:
            continue

        args = tool_info.args if tool_info else _message_args(message)
        xml_results.append(
            XmlToolResult(
                index=index,
                tool_name=tool_name,
                args=args,
                prior_xml=latest_xml,
            )
        )
        latest_xml = xml_content

    if len(xml_results) <= 1:
        return list(messages)

    latest_index = xml_results[-1].index
    summaries = {
        result.index: _summarize_tool_result(result)
        for result in xml_results
        if result.index != latest_index
    }

    return [
        _copy_message_with_content(message, summaries[index]) if index in summaries else message
        for index, message in enumerate(messages)
    ]


def _extract_tool_calls(message: Any) -> dict[str, ToolCallInfo]:
    if isinstance(message, AIMessage):
        raw_tool_calls = message.tool_calls
        additional_kwargs = message.additional_kwargs
    elif isinstance(message, Mapping):
        raw_tool_calls = message.get("tool_calls") or []
        additional_kwargs = message.get("additional_kwargs") or {}
    else:
        return {}
    if not raw_tool_calls:
        raw_tool_calls = additional_kwargs.get("tool_calls", []) if isinstance(additional_kwargs, Mapping) else []

    extracted: dict[str, ToolCallInfo] = {}
    for raw_call in raw_tool_calls:
        call_id, name, args = _parse_tool_call(raw_call)
        if call_id:
            extracted[call_id] = ToolCallInfo(name=name, args=args)
    return extracted


def _parse_tool_call(raw_call: Any) -> tuple[str | None, str | None, Mapping[str, Any]]:
    if not isinstance(raw_call, Mapping):
        return None, None, {}
    call_id = raw_call.get("id") or raw_call.get("tool_call_id")
    name = raw_call.get("name")
    args = raw_call.get("args") or {}

    function = raw_call.get("function")
    if isinstance(function, Mapping):
        name = name or function.get("name")
        args = args or function.get("arguments") or {}

    return (
        str(call_id) if call_id else None,
        str(name) if name else None,
        _normalize_args(args),
    )


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


def _message_content(message: Any) -> str | None:
    content = message.get("content") if isinstance(message, Mapping) else message.content
    if isinstance(content, str):
        return content
    return None


def _message_args(message: Any) -> Mapping[str, Any]:
    args = message.get("args") if isinstance(message, Mapping) else message.additional_kwargs.get("args")
    return _normalize_args(args)


def _tool_call_id(message: Any) -> str | None:
    value = message.get("tool_call_id") if isinstance(message, Mapping) else (
        message.tool_call_id if isinstance(message, ToolMessage) else None
    )
    return str(value) if value else None


def _tool_name(message: Any) -> str | None:
    value = message.get("name") if isinstance(message, Mapping) else message.name
    return str(value) if value else None


def _is_xml_content(content: str | None) -> bool:
    return _xml_content(content) is not None


def _xml_content(content: str | None) -> str | None:
    if not content:
        return None
    if content.lstrip().startswith("{"):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, Mapping) and isinstance(parsed.get("xml"), str):
            return parsed["xml"]
        return None
    if "<?xml" in content or "<hierarchy" in content or "</hierarchy>" in content or content.startswith("<h>"):
        return content
    return None


def _summarize_tool_result(result: XmlToolResult) -> str:
    if result.tool_name in {"tap_in_page", "navigate_to"}:
        return _summarize_tap(result)
    if result.tool_name == "swipe_up":
        return f"swipe up on {_format_xy(result.args)} to load more shops or content"
    if result.tool_name == "swipe_down":
        return f"swipe down on {_format_xy(result.args)} to reveal previous content or refresh"
    if result.tool_name == "navigate_back":
        return f"go back to {result.args.get('next_path') or 'the declared page'}"
    return "read current page XML"


def _summarize_tap(result: XmlToolResult) -> str:
    xy = _xy_tuple(result.args)
    target = _infer_tap_target(result.prior_xml, xy) if xy else None
    if not xy:
        return "tap to interact with the visible target"
    if not target:
        return f"tap on {_format_xy(result.args)} to interact with the visible target"
    return f"tap '{_escape_label(target)}' on {_format_xy(result.args)} {_tap_purpose(target)}"


def _tap_purpose(target: str) -> str:
    normalized = target.strip().lower()
    close_labels = {"关闭", "取消", "暂不", "以后再说", "不了", "close", "dismiss", "cancel", "not now"}
    subpage_labels = {
        "美食",
        "外卖",
        "酒店",
        "门票",
        "景点",
        "休闲玩乐",
        "电影",
        "打车",
        "丽人",
        "超市",
        "甜品饮品",
        "快餐",
        "小吃",
        "火锅",
        "烧烤",
        "川菜",
        "购物",
        "food",
        "takeout",
        "restaurants",
    }
    if normalized in close_labels or target.strip() in close_labels:
        return "to close a popup"
    if normalized in subpage_labels or target.strip() in subpage_labels:
        return "to load a subpage"
    return "to interact with the visible target"


def _infer_tap_target(xml_text: str | None, xy: tuple[int, int] | None) -> str | None:
    if not xml_text or xy is None:
        return None
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    x, y = xy
    candidates: list[tuple[int, str]] = []
    for node in root.iter():
        bounds = _parse_bounds(node.attrib.get("bounds", ""))
        if not bounds or not _bounds_contain(bounds, x, y):
            continue
        label = _node_label(node.attrib)
        if not label:
            continue
        left, top, right, bottom = bounds
        candidates.append(((right - left) * (bottom - top), label))

    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _parse_bounds(value: str) -> tuple[int, int, int, int] | None:
    match = BOUNDS_RE.fullmatch(value.strip())
    if not match:
        return None
    left, top, right, bottom = (int(part) for part in match.groups())
    return left, top, right, bottom


def _bounds_contain(bounds: tuple[int, int, int, int], x: int, y: int) -> bool:
    left, top, right, bottom = bounds
    return left <= x <= right and top <= y <= bottom


def _node_label(attributes: Mapping[str, str]) -> str | None:
    for key in ("text", "content-desc", "resource-id"):
        value = (attributes.get(key) or "").strip()
        if value:
            return value
    return None


def _xy_tuple(args: Mapping[str, Any]) -> tuple[int, int] | None:
    try:
        return int(args["x"]), int(args["y"])
    except (KeyError, TypeError, ValueError):
        return None


def _format_xy(args: Mapping[str, Any]) -> str:
    xy = _xy_tuple(args)
    if xy is None:
        return "(unknown, unknown)"
    return f"({xy[0]}, {xy[1]})"


def _escape_label(label: str) -> str:
    return label.replace("\\", "\\\\").replace("'", "\\'")


def _copy_message_with_content(message: Any, content: str) -> Any:
    if isinstance(message, Mapping):
        updated = dict(message)
        updated["content"] = content
        return updated
    if isinstance(message, BaseMessage):
        return message.model_copy(update={"content": content})
    raise TypeError(f"Unsupported message type: {type(message).__name__}")
