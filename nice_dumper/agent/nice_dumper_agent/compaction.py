"""Measure candidate XML compression opportunities for optimizer feedback."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any


_XML_DECLARATION_RE = re.compile(r"^\s*<\?xml[^?]*\?>\s*", re.IGNORECASE)
_INTERTAG_WHITESPACE_RE = re.compile(r">(\s+)<")
_IDENTITY_ATTRIBUTES = {"index", "package"}
_FALSE_DEFAULT_ATTRIBUTES = {
    "checkable",
    "checked",
    "clickable",
    "focusable",
    "focused",
    "long-clickable",
    "password",
    "scrollable",
    "selected",
}
_FUNCTION_STATE_ATTRIBUTES = {
    "checkable",
    "checked",
    "clickable",
    "long-clickable",
    "scrollable",
    "selected",
}


def analyze_xml_compaction(xml_text: str) -> dict[str, Any]:
    """Return advisory counts for whitespace and likely redundant attributes."""

    raw = str(xml_text or "")
    declaration = _XML_DECLARATION_RE.match(raw)
    declaration_characters = len(declaration.group(0)) if declaration else 0
    intertag_whitespace_characters = sum(
        len(match.group(1)) for match in _INTERTAG_WHITESPACE_RE.finditer(raw)
    )
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        return {
            "valid_xml": False,
            "parse_error": str(exc),
            "length": len(raw),
            "xml_declaration_characters": declaration_characters,
            "intertag_whitespace_characters": intertag_whitespace_characters,
        }

    elements = list(root.iter())
    whitespace_text_tail_characters = sum(
        len(value)
        for element in elements
        for value in (element.text, element.tail)
        if value and not value.strip()
    )
    attribute_frequency: Counter[str] = Counter()
    empty_count = 0
    identity_count = 0
    default_state_count = 0
    candidate_attribute_characters = 0
    meaningless_leaf_candidates = 0
    contractible_container_candidates = 0
    single_child_container_candidates = 0

    for element in elements:
        if element is root or _has_own_function_semantics(element):
            continue
        child_count = len(element)
        if child_count == 0:
            meaningless_leaf_candidates += 1
        else:
            contractible_container_candidates += 1
            if child_count == 1:
                single_child_container_candidates += 1

    for element in elements:
        for name, raw_value in element.attrib.items():
            value = str(raw_value or "")
            attribute_frequency[name] += 1
            reason = _redundant_attribute_reason(name, value)
            if reason is None:
                continue
            candidate_attribute_characters += _serialized_attribute_length(name, value)
            if reason == "empty":
                empty_count += 1
            elif reason == "identity":
                identity_count += 1
            else:
                default_state_count += 1

    compression_opportunity_characters = (
        declaration_characters
        + whitespace_text_tail_characters
        + candidate_attribute_characters
    )
    return {
        "valid_xml": True,
        "length": len(raw),
        "element_count": len(elements),
        "attribute_count": sum(attribute_frequency.values()),
        "xml_declaration_characters": declaration_characters,
        "intertag_whitespace_characters": intertag_whitespace_characters,
        "whitespace_text_tail_characters": whitespace_text_tail_characters,
        "empty_attribute_count": empty_count,
        "identity_attribute_count": identity_count,
        "default_state_attribute_count": default_state_count,
        "candidate_attribute_characters": candidate_attribute_characters,
        "compression_opportunity_characters": compression_opportunity_characters,
        "meaningless_leaf_candidate_count": meaningless_leaf_candidates,
        "contractible_container_candidate_count": contractible_container_candidates,
        "single_child_container_candidate_count": single_child_container_candidates,
        "top_attributes": dict(attribute_frequency.most_common(12)),
    }


def _redundant_attribute_reason(name: str, value: str) -> str | None:
    normalized = value.strip().lower()
    if not normalized:
        return "empty"
    if name in _IDENTITY_ATTRIBUTES:
        return "identity"
    if name in _FALSE_DEFAULT_ATTRIBUTES and normalized == "false":
        return "default-state"
    if name == "enabled" and normalized == "true":
        return "default-state"
    return None


def _serialized_attribute_length(name: str, value: str) -> int:
    # Leading space, name, equals, two quotes, and the value.
    return len(name) + len(value) + 4


def _has_own_function_semantics(element: ET.Element) -> bool:
    attrib = element.attrib
    if str(attrib.get("text") or "").strip():
        return True
    if str(attrib.get("content-desc") or "").strip():
        return True
    return any(
        str(attrib.get(name) or "").strip().lower() == "true"
        for name in _FUNCTION_STATE_ATTRIBUTES
    )
