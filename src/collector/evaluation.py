"""Score collector script output against an expected collection result."""

from collections.abc import Mapping
from typing import NotRequired, TypedDict

from src.common.utils.json import JsonValue, coerce_json
from src.page.script_types import ScriptResult


class EvaluationGrade(TypedDict):
    score: int
    errors: list[str]
    missing: list[str]
    execution_error: NotRequired[ScriptResult]


_VISUAL_PUNCTUATION = str.maketrans({
    "，": ",", "。": ".", "．": ".", "（": "(", "）": ")",
    "－": "-", "—": "-", "；": ";",
})


def _visible_text(value: str) -> str:
    """Normalize visual whitespace and punctuation without changing stored evidence."""
    return "".join(value.translate(_VISUAL_PUNCTUATION).split())


def _compare_visible(expected: JsonValue, actual: JsonValue, path: str,
                     errors: list[str], missing: list[str]) -> int:
    """Match visible evidence to distinct result entries, allowing extra data and fuller text."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        matched = 0
        for key, value in expected.items():
            if key not in actual:
                missing.append(f"{path}.{key}")
            else:
                matched += _compare_visible(value, actual[key], f"{path}.{key}", errors, missing)
        return matched
    if isinstance(expected, list) and isinstance(actual, list):
        candidates: list[list[int]] = []
        for value in expected:
            matches = []
            for index, item in enumerate(actual):
                item_errors: list[str] = []
                item_missing: list[str] = []
                _compare_visible(value, item, path, item_errors, item_missing)
                if not item_errors and not item_missing:
                    matches.append(index)
            candidates.append(matches)
        # Maximum bipartite matching prevents broad partial labels stealing a specific match.
        owners: dict[int, int] = {}

        def assign(index: int, seen: set[int]) -> bool:
            for target in candidates[index]:
                if target in seen:
                    continue
                seen.add(target)
                if target not in owners or assign(owners[target], seen):
                    owners[target] = index
                    return True
            return False

        for index in range(len(expected)):
            if not assign(index, set()):
                missing.append(f"{path}[{index}]")
        return len(owners)
    if isinstance(expected, str) and isinstance(actual, str):
        matches = _visible_text(expected) in _visible_text(actual)
    else:
        matches = type(expected) is type(actual) and expected == actual
        if type(expected) in (int, float) and type(actual) in (int, float):
            matches = expected == actual
    if matches:
        return 1
    errors.append(path)
    return 0


def eval_collection(r1: object, r2: object) -> EvaluationGrade:
    expected: JsonValue = coerce_json(r1)
    actual: JsonValue = coerce_json(r2)
    if isinstance(actual, Mapping) and actual.get("ok") is True and "result" in actual:
        actual = actual["result"]
    if isinstance(actual, Mapping) and actual.get("ok") is False:
        return {"score": 0, "errors": ["result"], "missing": []}

    errors: list[str] = []
    missing: list[str] = []
    matched: int = _compare_visible(expected, actual, "result", errors, missing)
    failures: int = len(errors) + len(missing)
    total: int = matched + failures
    score: int = 100 if failures == 0 else min(99, round((matched / total) * 100))
    return {"score": score, "errors": errors, "missing": missing}
