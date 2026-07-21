"""Deterministic similarity scoring for optimized UIAutomator XML."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET


_SPACE_RE = re.compile(r"\s+")
_BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")
_ACTION_ATTRIBUTES = ("k", "s", "sel", "chk")


class PageSimilarityError(ValueError):
    """Raised when a page snapshot cannot be compared."""


@dataclass(frozen=True)
class PageSimilarityResult:
    """Internal comparison result."""

    similarity: float
    semantic_similarity: float
    structural_similarity: float


@dataclass(frozen=True)
class _Node:
    attributes: dict[str, str]
    width: int
    height: int


class PageSimilarityEvaluator:
    """Compare page content while ignoring absolute node coordinates."""

    semantic_weight = 0.8

    def compare(self, before_xml: str, after_xml: str) -> PageSimilarityResult:
        before = self._parse(before_xml)
        after = self._parse(after_xml)

        before_semantic = Counter(
            signature for node in before if (signature := self._semantic_signature(node)) is not None
        )
        after_semantic = Counter(
            signature for node in after if (signature := self._semantic_signature(node)) is not None
        )
        before_structural = Counter(self._structural_signature(node, before) for node in before)
        after_structural = Counter(self._structural_signature(node, after) for node in after)

        structural = self._dice(before_structural, after_structural)
        if not before_semantic and not after_semantic:
            semantic = structural
            similarity = structural
        else:
            semantic = self._dice(before_semantic, after_semantic)
            similarity = self.semantic_weight * semantic + (1.0 - self.semantic_weight) * structural
        return PageSimilarityResult(
            similarity=round(max(0.0, min(1.0, similarity)), 6),
            semantic_similarity=round(semantic, 6),
            structural_similarity=round(structural, 6),
        )

    def _parse(self, xml_text: str) -> list[_Node]:
        if not isinstance(xml_text, str) or not xml_text.strip():
            raise PageSimilarityError("Page XML is empty.")
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise PageSimilarityError("Page XML is invalid.") from exc

        nodes: list[_Node] = []
        for element in root.iter():
            if element is root:
                continue
            bounds = self._bounds(element.attrib.get("b") or element.attrib.get("bounds") or "")
            if bounds is None:
                continue
            left, top, right, bottom = bounds
            attributes = {
                key: self._normalize(value)
                for key, value in element.attrib.items()
                if key not in {"b", "bounds"} and self._normalize(value)
            }
            nodes.append(_Node(attributes=attributes, width=right - left, height=bottom - top))
        if not nodes:
            raise PageSimilarityError("Page XML contains no comparable nodes.")
        return nodes

    def _semantic_signature(self, node: _Node) -> tuple[str, ...] | None:
        labels = tuple(node.attributes.get(key, "") for key in ("t", "text", "d", "content-desc", "r", "resource-id"))
        if not any(labels):
            return None
        actions = tuple(node.attributes.get(key, "") for key in _ACTION_ATTRIBUTES)
        class_name = node.attributes.get("c") or node.attributes.get("class", "")
        return (*labels, class_name, *actions)

    def _structural_signature(self, node: _Node, page: list[_Node]) -> tuple[str, ...]:
        page_width = max(item.width for item in page)
        page_height = max(item.height for item in page)
        class_name = node.attributes.get("c") or node.attributes.get("class", "")
        actions = tuple(node.attributes.get(key, "") for key in _ACTION_ATTRIBUTES)
        return (
            class_name,
            *actions,
            str(self._size_bucket(node.width, page_width)),
            str(self._size_bucket(node.height, page_height)),
        )

    @staticmethod
    def _size_bucket(value: int, maximum: int) -> int:
        return min(12, max(1, round(value / maximum * 12))) if maximum > 0 else 1

    @staticmethod
    def _bounds(value: str) -> tuple[int, int, int, int] | None:
        match = _BOUNDS_RE.match(value)
        if match is None:
            return None
        left, top, right, bottom = map(int, match.groups())
        return (left, top, right, bottom) if right > left and bottom > top else None

    @staticmethod
    def _normalize(value: str) -> str:
        return _SPACE_RE.sub(" ", value or "").strip().casefold()

    @staticmethod
    def _dice(left: Counter[tuple[str, ...]], right: Counter[tuple[str, ...]]) -> float:
        total = sum(left.values()) + sum(right.values())
        if total == 0:
            return 1.0
        overlap = sum((left & right).values())
        return 2.0 * overlap / total


__all__ = ["PageSimilarityError", "PageSimilarityEvaluator", "PageSimilarityResult"]
