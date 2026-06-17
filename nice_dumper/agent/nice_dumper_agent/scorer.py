"""Score optimized XML against baseline recognized function regions."""

from __future__ import annotations

import re

from .models import FunctionRegion, MatchResult, RecognizerResult, ScoreResult

BOUNDS_RE = re.compile(r"^\[(\d+),(\d+)\]\[(\d+),(\d+)\]$")


def score_regions(xml0: str, xml1: str, l0: RecognizerResult, l1: RecognizerResult) -> ScoreResult:
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
    score = 100.0 * (0.80 * fidelity + 0.20 * compression) - 60.0 * missing_penalty
    return ScoreResult(
        score=score,
        fidelity=fidelity,
        compression=compression,
        missing_count=missing_count,
        missing_penalty=missing_penalty,
        matches=matches,
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
