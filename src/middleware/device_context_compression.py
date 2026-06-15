"""Device context compression middleware for LangChain agents."""

from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from langchain.agents.middleware.types import AgentMiddleware

XML_DEVICE_TOOLS = {"uiautomate", "tap", "swipe_up", "swipe_down", "swipe_back"}
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

        if not tool_name or tool_name not in XML_DEVICE_TOOLS or not _is_xml_content(content):
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
        latest_xml = content

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
    raw_tool_calls = _get_value(message, "tool_calls") or []
    if not raw_tool_calls:
        additional_kwargs = _get_value(message, "additional_kwargs") or {}
        raw_tool_calls = additional_kwargs.get("tool_calls", []) if isinstance(additional_kwargs, Mapping) else []

    extracted: dict[str, ToolCallInfo] = {}
    for raw_call in raw_tool_calls:
        call_id, name, args = _parse_tool_call(raw_call)
        if call_id:
            extracted[call_id] = ToolCallInfo(name=name, args=args)
    return extracted


def _parse_tool_call(raw_call: Any) -> tuple[str | None, str | None, Mapping[str, Any]]:
    call_id = _get_value(raw_call, "id") or _get_value(raw_call, "tool_call_id")
    name = _get_value(raw_call, "name")
    args = _get_value(raw_call, "args") or {}

    function = _get_value(raw_call, "function")
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
    content = _get_value(message, "content")
    if isinstance(content, str):
        return content
    return None


def _message_args(message: Any) -> Mapping[str, Any]:
    args = _get_value(message, "args")
    return _normalize_args(args)


def _tool_call_id(message: Any) -> str | None:
    value = _get_value(message, "tool_call_id")
    return str(value) if value else None


def _tool_name(message: Any) -> str | None:
    value = _get_value(message, "name")
    return str(value) if value else None


def _get_value(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _is_xml_content(content: str | None) -> bool:
    if not content:
        return False
    return "<?xml" in content or "<hierarchy" in content or "</hierarchy>" in content


def _summarize_tool_result(result: XmlToolResult) -> str:
    if result.tool_name == "tap":
        return _summarize_tap(result)
    if result.tool_name == "swipe_up":
        return f"swipe up on {_format_xy(result.args)} to load more shops or content"
    if result.tool_name == "swipe_down":
        return f"swipe down on {_format_xy(result.args)} to reveal previous content or refresh"
    if result.tool_name == "swipe_back":
        return "go back from the current page"
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
    if hasattr(message, "model_copy"):
        return message.model_copy(update={"content": content})
    if hasattr(message, "copy"):
        try:
            return message.copy(update={"content": content})
        except TypeError:
            pass
    updated = copy.copy(message)
    setattr(updated, "content", content)
    return updated
