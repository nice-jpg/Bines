import re
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from typing import Final

type Rectangle = tuple[int, int, int, int]

_ACTION_ATTRS: Final[tuple[str, str, str]] = ("clickable", "long-clickable", "scrollable")
_LABEL_ATTRS: Final[set[str]] = {"text", "content-desc", "resource-id", "bounds"}
_TRUE_STATE_ATTRS: Final[set[str]] = {
    "clickable",
    "long-clickable",
    "scrollable",
    "checkable",
    "checked",
    "focusable",
    "focused",
    "password",
    "selected",
}
_ROLE_CLASSES: Final[set[str]] = {
    "android.widget.ImageView",
    "android.widget.ImageButton",
    "android.widget.Button",
    "android.widget.EditText",
    "android.widget.CheckBox",
    "android.widget.RadioButton",
    "android.widget.Switch",
}


def _rect(value: str | None) -> Rectangle | None:
    match: re.Match[str] | None = re.fullmatch(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", value or "")
    return (int(match[1]), int(match[2]), int(match[3]), int(match[4])) if match else None


def _area(rect: Rectangle | None) -> int:
    return max(0, rect[2] - rect[0]) * max(0, rect[3] - rect[1]) if rect else 0


def _intersection_area(first: Rectangle | None, second: Rectangle | None) -> int:
    if first is None or second is None:
        return 0
    return max(0, min(first[2], second[2]) - max(first[0], second[0])) * max(
        0, min(first[3], second[3]) - max(first[1], second[1])
    )


def _actionable_count(node: ET.Element) -> int:
    return sum(1 for item in node.iter() if any(item.attrib.get(name) == "true" for name in _ACTION_ATTRS))


def _is_legacy_hidden_candidate(node: ET.Element, parent: ET.Element, siblings: Sequence[ET.Element]) -> bool:
    resource_id: str = node.attrib.get("resource-id", "").lower()
    semantic_match: bool = (
        "pull_loading" in resource_id or "pull-loading" in resource_id or "preload" in resource_id
    )
    node_rect: Rectangle | None = _rect(node.attrib.get("bounds"))
    parent_rect: Rectangle | None = _rect(parent.attrib.get("bounds"))
    parent_area: int = _area(parent_rect)
    coverage: float = _intersection_area(node_rect, parent_rect) / parent_area if parent_area else 0.0
    active_overlap: bool = any(
        sibling is not node
        and _intersection_area(node_rect, _rect(sibling.attrib.get("bounds"))) > 0
        and _actionable_count(sibling) >= 2
        for sibling in siblings
    )
    return semantic_match and coverage >= 0.95 and _actionable_count(node) == 0 and active_overlap


def _prune_hidden(parent: ET.Element) -> None:
    children: list[ET.Element[str]] = list(parent)
    for child in children:
        if child.attrib.get("visible-to-user") == "false" or _is_legacy_hidden_candidate(
            child, parent, children
        ):
            parent.remove(child)
        else:
            _prune_hidden(child)


def _is_contractible(node: ET.Element) -> bool:
    if len(node) != 1:
        return False
    if node.attrib.get("text") or node.attrib.get("content-desc") or node.attrib.get("resource-id"):
        return False
    if node.attrib.get("class") in _ROLE_CLASSES or node.attrib.get("enabled") == "false":
        return False
    return not any(node.attrib.get(name) == "true" for name in _TRUE_STATE_ATTRS)


def _contract_single_child_containers(parent: ET.Element) -> None:
    for child in list(parent):
        _contract_single_child_containers(child)
    index: int = 0
    while index < len(parent):
        child = parent[index]
        if _is_contractible(child):
            grandchild: ET.Element[str] = child[0]
            parent.remove(child)
            parent.insert(index, grandchild)
            _contract_single_child_containers(grandchild)
        else:
            index += 1


def _short_class(value: str) -> str:
    for prefix in ("android.widget.", "android.view.", "android.webkit."):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def _needs_class(node: ET.Element) -> bool:
    return (
        len(node) == 0
        or bool(node.attrib.get("text") or node.attrib.get("content-desc") or node.attrib.get("resource-id"))
        or any(node.attrib.get(name) == "true" for name in _ACTION_ATTRS)
    )


def _compact_attributes(node: ET.Element) -> None:
    kept: dict[str, str] = {}
    keep_class: bool = _needs_class(node)
    for name, value in node.attrib.items():
        if not value:
            continue
        if name == "resource-id":
            kept[name] = value
        elif name == "class" and keep_class:
            kept[name] = _short_class(value)
        elif name in _LABEL_ATTRS:
            kept[name] = value
        elif name in _TRUE_STATE_ATTRS and value == "true":
            kept[name] = value
        elif name == "enabled" and value == "false":
            kept[name] = value
    node.attrib.clear()
    node.attrib.update(kept)
    if node.text is None or not node.text.strip():
        node.text = None
    if node.tail is None or not node.tail.strip():
        node.tail = None
    for child in node:
        _compact_attributes(child)


def optimize(xml_text: str) -> str:
    root: ET.Element[str] = ET.fromstring(xml_text)
    _prune_hidden(root)
    _contract_single_child_containers(root)
    _compact_attributes(root)
    result: str = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    return re.sub(r">\s+<", "><", result).strip()
