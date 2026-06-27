"""Recognizer sub-agent for extracting page function regions from XML."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Mapping

from .models import FunctionRegion, RecognizerResult
from .prompts import RECOGNIZER_SYSTEM_PROMPT
from .scorer import parse_bounds


@dataclass(frozen=True)
class HiddenSubtreeCandidate:
    path: str
    resource_id: str
    bounds: str
    reason: str
    descendant_count: int
    actionable_descendant_count: int
    overlapping_sibling_actionable_count: int
    estimated_characters: int
    sample_labels: list[str]


def build_recognizer_agent(model: Any):
    """Build the recognizer as a LangChain create_agent instance."""

    from langchain.agents import create_agent

    return create_agent(
        model=model,
        tools=[],
        system_prompt=RECOGNIZER_SYSTEM_PROMPT,
        name="recognizer",
    )


def recognize_functions(
    model: Any,
    xml_text: str,
    *,
    max_retries: int = 1,
    allow_fallback: bool = True,
) -> RecognizerResult:
    """Run recognizer agent and parse the JSON function list."""

    agent = build_recognizer_agent(model)
    recognizer_xml = prepare_recognizer_xml(xml_text)
    prompt = "Analyze this XML and return JSON only:\n\n" + recognizer_xml
    last_output = ""
    last_error = ""
    for attempt in range(max_retries + 1):
        content = prompt if attempt == 0 else _retry_prompt(recognizer_xml, last_output, last_error)
        try:
            state = agent.invoke({"messages": [{"role": "user", "content": content}]})
            last_output = _latest_text(state)
            parsed = parse_recognizer_output(last_output)
            if parsed.ok:
                return parsed
            last_error = str(parsed.error or "invalid recognizer output")
        except Exception as exc:  # noqa: BLE001 - surface structured recognizer errors.
            last_error = f"{type(exc).__name__}: {exc}"
    if allow_fallback:
        fallback = recognize_functions_locally(recognizer_xml)
        if fallback.ok:
            return RecognizerResult(
                functions=fallback.functions,
                raw_output=f"local fallback used after recognizer error: {last_error}",
            )
    return RecognizerResult(functions=[], raw_output=last_output, error=last_error or "recognizer failed")


def recognize_functions_locally(xml_text: str) -> RecognizerResult:
    """Heuristic XML-only recognizer used when the LLM recognizer is unavailable."""

    try:
        root = ET.fromstring(prepare_recognizer_xml(xml_text))
    except ET.ParseError as exc:
        return RecognizerResult(functions=[], raw_output=str(xml_text or ""), error=f"invalid XML: {exc}")

    functions: list[FunctionRegion] = []
    seen: set[tuple[str, str]] = set()
    for node in root.iter():
        attrib = node.attrib
        bounds = str(attrib.get("bounds") or "").strip()
        if parse_bounds(bounds) is None:
            continue
        label = _node_label(attrib)
        if not label:
            continue
        if not _looks_functional(attrib, label):
            continue
        key = (bounds, label)
        if key in seen:
            continue
        seen.add(key)
        functions.append(FunctionRegion(bounds=bounds, label=label))
    return RecognizerResult(functions=functions, raw_output="local fallback")


def prepare_recognizer_xml(xml_text: str) -> str:
    """Remove subtrees known to be invisible before function recognition."""

    raw = str(xml_text or "")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return raw

    _remove_hidden_children(root)
    return ET.tostring(root, encoding="unicode")


def analyze_hidden_subtrees(xml_text: str) -> list[HiddenSubtreeCandidate]:
    """Describe maximal hidden subtrees using the recognizer's visibility rules."""

    try:
        root = ET.fromstring(str(xml_text or ""))
    except ET.ParseError:
        return []

    candidates: list[HiddenSubtreeCandidate] = []

    def visit(parent: ET.Element, parent_path: str) -> None:
        children = list(parent)
        for index, child in enumerate(children):
            path = f"{parent_path}/{index}" if parent_path else str(index)
            reason = _hidden_subtree_reason(child, parent, children)
            if reason is not None:
                candidates.append(_hidden_candidate(child, siblings=children, path=path, reason=reason))
                continue
            visit(child, path)

    visit(root, "")
    return candidates


def _remove_hidden_children(parent: ET.Element) -> None:
    children = list(parent)
    for child in children:
        if _is_hidden_subtree(child, parent, children):
            parent.remove(child)
            continue
        _remove_hidden_children(child)


def _is_hidden_subtree(
    node: ET.Element,
    parent: ET.Element,
    siblings: list[ET.Element],
) -> bool:
    return _hidden_subtree_reason(node, parent, siblings) is not None


def _hidden_subtree_reason(
    node: ET.Element,
    parent: ET.Element,
    siblings: list[ET.Element],
) -> str | None:
    visibility = _explicit_visibility(node.attrib)
    if visibility is False:
        return "visible-to-user=false"
    if visibility is True:
        return None
    if _looks_like_inactive_preloaded_layer(node, parent, siblings):
        return "inactive-preloaded-pull-layer"
    return None


def _explicit_visibility(attrib: Mapping[str, str]) -> bool | None:
    for key in ("visible-to-user", "visible_to_user"):
        if key not in attrib:
            continue
        return _truthy(attrib.get(key))
    return None


def _looks_like_inactive_preloaded_layer(
    node: ET.Element,
    parent: ET.Element,
    siblings: list[ET.Element],
) -> bool:
    resource_id = str(node.attrib.get("resource-id") or "").lower()
    resource_tail = resource_id.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    inactive_markers = (
        "pull_loading_bg_container",
        "pull_loading_container",
        "preloaded_pull_container",
        "preload_pull_container",
    )
    if not any(marker in resource_tail for marker in inactive_markers):
        return False

    node_bounds = parse_bounds(str(node.attrib.get("bounds") or ""))
    parent_bounds = parse_bounds(str(parent.attrib.get("bounds") or ""))
    if node_bounds is None or parent_bounds is None:
        return False
    if _coverage(node_bounds, parent_bounds) < 0.95:
        return False
    if _actionable_descendant_count(node) != 0:
        return False

    for sibling in siblings:
        if sibling is node:
            continue
        sibling_bounds = parse_bounds(str(sibling.attrib.get("bounds") or ""))
        if sibling_bounds is None:
            continue
        if _coverage(sibling_bounds, node_bounds) < 0.95:
            continue
        if _actionable_descendant_count(sibling) >= 2:
            return True
    return False


def _actionable_descendant_count(node: ET.Element) -> int:
    return sum(
        1
        for descendant in node.iter()
        if _truthy(descendant.attrib.get("clickable"))
        or _truthy(descendant.attrib.get("long-clickable"))
        or _truthy(descendant.attrib.get("scrollable"))
    )


def _hidden_candidate(
    node: ET.Element,
    *,
    siblings: list[ET.Element],
    path: str,
    reason: str,
) -> HiddenSubtreeCandidate:
    node_bounds = parse_bounds(str(node.attrib.get("bounds") or ""))
    overlapping_actionable = 0
    if node_bounds is not None:
        for sibling in siblings:
            if sibling is node:
                continue
            sibling_bounds = parse_bounds(str(sibling.attrib.get("bounds") or ""))
            if sibling_bounds is None or _coverage(sibling_bounds, node_bounds) < 0.95:
                continue
            overlapping_actionable = max(
                overlapping_actionable,
                _actionable_descendant_count(sibling),
            )
    labels: list[str] = []
    for descendant in node.iter():
        label = _node_label(descendant.attrib)
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= 8:
            break
    return HiddenSubtreeCandidate(
        path=path,
        resource_id=str(node.attrib.get("resource-id") or ""),
        bounds=str(node.attrib.get("bounds") or ""),
        reason=reason,
        descendant_count=sum(1 for _ in node.iter()),
        actionable_descendant_count=_actionable_descendant_count(node),
        overlapping_sibling_actionable_count=overlapping_actionable,
        estimated_characters=len(ET.tostring(node, encoding="unicode")),
        sample_labels=labels,
    )


def _coverage(
    target: tuple[int, int, int, int],
    cover: tuple[int, int, int, int],
) -> float:
    target_left, target_top, target_right, target_bottom = target
    cover_left, cover_top, cover_right, cover_bottom = cover
    target_area = (target_right - target_left) * (target_bottom - target_top)
    if target_area <= 0:
        return 0.0
    width = max(0, min(target_right, cover_right) - max(target_left, cover_left))
    height = max(0, min(target_bottom, cover_bottom) - max(target_top, cover_top))
    return (width * height) / target_area


def parse_recognizer_output(output: str) -> RecognizerResult:
    """Parse and validate recognizer JSON."""

    raw = str(output or "").strip()
    try:
        parsed = json.loads(_extract_json(raw))
    except json.JSONDecodeError as exc:
        return RecognizerResult(functions=[], raw_output=raw, error=f"invalid recognizer JSON: {exc}")
    if not isinstance(parsed, Mapping):
        return RecognizerResult(functions=[], raw_output=raw, error="recognizer output must be a JSON object")
    raw_functions = parsed.get("functions")
    if not isinstance(raw_functions, list):
        return RecognizerResult(functions=[], raw_output=raw, error="recognizer output missing functions list")

    functions: list[FunctionRegion] = []
    for index, item in enumerate(raw_functions):
        if not isinstance(item, Mapping):
            return RecognizerResult(functions=[], raw_output=raw, error=f"function #{index} is not an object")
        bounds = str(item.get("bounds") or "").strip()
        label = str(item.get("label") or "").strip()
        if not label:
            return RecognizerResult(functions=[], raw_output=raw, error=f"function #{index} missing label")
        if parse_bounds(bounds) is None:
            return RecognizerResult(functions=[], raw_output=raw, error=f"function #{index} has invalid bounds")
        functions.append(FunctionRegion(bounds=bounds, label=label))
    return RecognizerResult(functions=functions, raw_output=raw)


def _extract_json(raw: str) -> str:
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    if raw.startswith("{"):
        return raw
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    return match.group(0) if match else raw


def _latest_text(state: Mapping[str, Any]) -> str:
    messages = state.get("messages") if isinstance(state, Mapping) else None
    if not messages:
        return ""
    latest = messages[-1]
    content = getattr(latest, "content", None)
    if content is None and isinstance(latest, Mapping):
        content = latest.get("content")
    return content if isinstance(content, str) else str(content or "")


def _retry_prompt(xml_text: str, last_output: str, last_error: str) -> str:
    return (
        "The previous recognizer output was invalid.\n"
        f"Error: {last_error}\n"
        f"Previous output: {last_output}\n"
        "Return valid JSON only for this XML:\n\n"
        + xml_text
    )


def _node_label(attrib: Mapping[str, str]) -> str:
    for key in ("text", "content-desc"):
        value = _clean_label(attrib.get(key, ""))
        if value:
            return value
    resource_id = str(attrib.get("resource-id") or "").strip()
    if resource_id:
        tail = resource_id.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        tail = tail.replace("_", " ").replace("-", " ").strip()
        if tail:
            return tail
    return ""


def _clean_label(value: str) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _looks_functional(attrib: Mapping[str, str], label: str) -> bool:
    if _truthy(attrib.get("clickable")) or _truthy(attrib.get("long-clickable")):
        return True
    if _truthy(attrib.get("scrollable")) or _truthy(attrib.get("focusable")):
        return True
    class_name = str(attrib.get("class") or "").lower()
    if any(part in class_name for part in ("button", "edittext", "tab", "checkbox", "switch")):
        return True
    resource_id = str(attrib.get("resource-id") or "").lower()
    if any(part in resource_id for part in ("tab", "search", "button", "btn", "entry", "channel", "nav")):
        return True
    return len(label) <= 24 and bool(str(attrib.get("content-desc") or "").strip())


def _truthy(value: object) -> bool:
    return str(value).strip().lower() == "true"
