"""Score optimized XML against baseline recognized function regions."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Protocol, Sequence

from .models import FunctionRegion, MatchResult, RecognizerResult, ScoreResult

BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")
HIDDEN_PRUNING_MAX_REWARD = 30.0


class HiddenCandidateLike(Protocol):
    path: str
    resource_id: str


def score_regions(
    xml0: str,
    xml1: str,
    l0: RecognizerResult,
    l1: RecognizerResult,
    hidden_candidates: Sequence[HiddenCandidateLike] = (),
) -> ScoreResult:
    """Compute fidelity/compression score using L0 as ground truth."""

    baseline = l0.functions
    optimized = l1.functions
    matches: list[MatchResult] = []
    missing_count = 0
    item_scores: list[float] = []

    for source in baseline:
        candidates = [_candidate for _candidate in optimized if _labels_match(source.label, _candidate.label)]
        best_target: FunctionRegion | None = None
        best_iou = 0.0
        for candidate in candidates:
            value = bounds_iou(source.bounds, candidate.bounds)
            if value > best_iou:
                best_iou = value
                best_target = candidate
        if best_target is None:
            missing_count += 1
            item_scores.append(0.0)
            matches.append(MatchResult(source=source, target=None, iou=0.0))
        else:
            item_scores.append(best_iou)
            matches.append(MatchResult(source=source, target=best_target, iou=best_iou))

    fidelity = sum(item_scores) / len(item_scores) if item_scores else 1.0
    compression = _clamp(1.0 - (len(xml1) / max(len(xml0), 1)), 0.0, 1.0)
    missing_penalty = missing_count / max(len(baseline), 1)
    hidden_scores = _hidden_node_pruning_scores(xml0, xml1, hidden_candidates)
    hidden_pruning = (
        sum(hidden_scores) / len(hidden_scores)
        if hidden_scores
        else 0.0
    )
    hidden_pruning_reward = HIDDEN_PRUNING_MAX_REWARD * hidden_pruning
    score = (
        100.0 * (0.80 * fidelity + 0.20 * compression)
        - 60.0 * missing_penalty
        + hidden_pruning_reward
    )
    return ScoreResult(
        score=score,
        fidelity=fidelity,
        compression=compression,
        missing_count=missing_count,
        missing_penalty=missing_penalty,
        matches=matches,
        hidden_pruning=hidden_pruning,
        hidden_pruning_reward=hidden_pruning_reward,
        hidden_subtree_count=len(hidden_candidates),
        hidden_candidate_count=len(hidden_scores),
        hidden_removed_count=sum(value >= 0.95 for value in hidden_scores),
    )


def bounds_iou(left_bounds: str, right_bounds: str) -> float:
    left = parse_bounds(left_bounds)
    right = parse_bounds(right_bounds)
    if left is None or right is None:
        return 0.0
    left_area = _area(left)
    right_area = _area(right)
    if left_area <= 0 or right_area <= 0:
        return 0.0
    ix1 = max(left[0], right[0])
    iy1 = max(left[1], right[1])
    ix2 = min(left[2], right[2])
    iy2 = min(left[3], right[3])
    intersection = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = left_area + right_area - intersection
    return intersection / union if union > 0 else 0.0


def parse_bounds(value: str) -> tuple[int, int, int, int] | None:
    match = BOUNDS_RE.match(str(value or "").strip())
    if not match:
        return None
    left, top, right, bottom = (int(part) for part in match.groups())
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _labels_match(left: str, right: str) -> bool:
    normalized_left = _normalize_label(left)
    normalized_right = _normalize_label(right)
    if not normalized_left or not normalized_right:
        return False
    return (
        normalized_left == normalized_right
        or normalized_left in normalized_right
        or normalized_right in normalized_left
    )


def _normalize_label(value: str) -> str:
    return "".join(str(value or "").strip().lower().split())


def _area(bounds: tuple[int, int, int, int]) -> int:
    return (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _hidden_node_pruning_scores(
    xml0: str,
    xml1: str,
    candidates: Sequence[HiddenCandidateLike],
) -> list[float]:
    if not candidates:
        return []
    try:
        root = ET.fromstring(xml0)
        optimized_root = ET.fromstring(xml1)
    except ET.ParseError:
        return []

    optimized_nodes = [_node_fingerprint(node) for node in optimized_root.iter()]
    scores: list[float] = []
    for candidate in candidates:
        node = _element_at_path(root, candidate.path)
        if node is None:
            continue
        for hidden_node in node.iter():
            hidden_fingerprint = _node_fingerprint(hidden_node)
            survival = max(
                (
                    _node_survival(hidden_fingerprint, optimized_fingerprint)
                    for optimized_fingerprint in optimized_nodes
                ),
                default=0.0,
            )
            scores.append(1.0 - survival)
    return scores


def _element_at_path(root: ET.Element, path: str) -> ET.Element | None:
    current = root
    if not str(path or "").strip():
        return current
    try:
        for raw_index in str(path).split("/"):
            current = list(current)[int(raw_index)]
    except (IndexError, ValueError):
        return None
    return current


def _node_fingerprint(element: ET.Element) -> tuple[str, tuple[str, ...], str, str]:
    attrib = element.attrib
    bounds = str(attrib.get("bounds") or attrib.get("b") or "").strip()
    labels = tuple(
        _normalize_label(value)
        for value in (
            attrib.get("text"),
            attrib.get("t"),
            attrib.get("content-desc"),
            attrib.get("d"),
            attrib.get("label"),
            attrib.get("l"),
        )
        if _normalize_label(value)
    )
    resource_id = _resource_tail(
        str(attrib.get("resource-id") or attrib.get("r") or "")
    ).lower()
    class_name = _class_tail(str(attrib.get("class") or attrib.get("c") or "")).lower()
    return bounds, labels, resource_id, class_name


def _node_survival(
    hidden: tuple[str, tuple[str, ...], str, str],
    optimized: tuple[str, tuple[str, ...], str, str],
) -> float:
    hidden_bounds, hidden_labels, hidden_resource_id, hidden_class = hidden
    optimized_bounds, optimized_labels, optimized_resource_id, optimized_class = optimized
    if not hidden_bounds or hidden_bounds != optimized_bounds:
        return 0.0
    if hidden_labels:
        if any(
            _labels_match(hidden_label, optimized_label)
            for hidden_label in hidden_labels
            for optimized_label in optimized_labels
        ):
            return 1.0
    if hidden_resource_id:
        if hidden_resource_id == optimized_resource_id:
            return 0.85 if hidden_labels else 1.0
        if hidden_class and hidden_class == optimized_class:
            return 0.65
        return 0.0
    if hidden_class:
        if hidden_class == optimized_class:
            return 0.65 if hidden_labels else 1.0
        return 0.0
    if not optimized_labels and not optimized_resource_id and not optimized_class:
        return 0.25
    return 0.0


def _resource_tail(resource_id: str) -> str:
    return str(resource_id or "").strip().rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _class_tail(class_name: str) -> str:
    return str(class_name or "").strip().rsplit(".", 1)[-1]
