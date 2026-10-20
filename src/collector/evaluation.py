"""Score collector script output against an expected collection result."""

from collections.abc import Mapping
from typing import NotRequired, TypedDict

from src.common.utils.json import JsonValue, coerce_json, compare_json
from src.page.script_types import ScriptResult


class EvaluationGrade(TypedDict):
    score: int
    errors: list[str]
    missing: list[str]
    execution_error: NotRequired[ScriptResult]


def eval_collection(r1: object, r2: object) -> EvaluationGrade:
    expected: JsonValue = coerce_json(r1)
    actual: JsonValue = coerce_json(r2)
    if isinstance(actual, Mapping) and actual.get("ok") is True and "result" in actual:
        actual = actual["result"]
    if isinstance(actual, Mapping) and actual.get("ok") is False:
        return {"score": 0, "errors": ["result"], "missing": []}

    errors: list[str] = []
    missing: list[str] = []
    matched: int = compare_json(expected, actual, "result", errors, missing)
    failures: int = len(errors) + len(missing)
    total: int = matched + failures
    score: int = 100 if total == 0 else round((matched / total) * 100)
    return {"score": score, "errors": errors, "missing": missing}
