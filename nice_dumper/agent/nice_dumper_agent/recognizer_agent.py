"""Recognizer sub-agent for extracting page function regions from XML."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Mapping

from .models import FunctionRegion, RecognizerResult
from .prompts import RECOGNIZER_SYSTEM_PROMPT
from .scorer import parse_bounds


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
    prompt = "Analyze this XML and return JSON only:\n\n" + xml_text
    last_output = ""
    last_error = ""
    for attempt in range(max_retries + 1):
        content = prompt if attempt == 0 else _retry_prompt(xml_text, last_output, last_error)
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
        fallback = recognize_functions_locally(xml_text)
        if fallback.ok:
            return RecognizerResult(
                functions=fallback.functions,
                raw_output=f"local fallback used after recognizer error: {last_error}",
            )
    return RecognizerResult(functions=[], raw_output=last_output, error=last_error or "recognizer failed")


def recognize_functions_locally(xml_text: str) -> RecognizerResult:
    """Heuristic XML-only recognizer used when the LLM recognizer is unavailable."""

    try:
        root = ET.fromstring(xml_text)
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
