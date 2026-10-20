"""Validated JSON values at serialization boundaries."""

import json
from collections.abc import Mapping
from typing import TypeGuard

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


def json_value(value: object) -> JsonValue:
    """Validate unknown data without coercing unsupported values to strings."""
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if _is_sequence(value):
        return [json_value(item) for item in value]
    if _is_mapping(value):
        result: JsonObject = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            result[key] = json_value(item)
        return result
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def json_object(value: object) -> JsonObject:
    result: JsonValue = json_value(value)
    if not isinstance(result, dict):
        raise TypeError("Expected a JSON object")
    return result


def load_json(text: str) -> JsonValue:
    value: object = json.loads(text)
    return json_value(value)


def _is_sequence(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def coerce_json(value: object) -> JsonValue:
    if isinstance(value, str):
        try:
            return load_json(value)
        except json.JSONDecodeError:
            return value
    return json_value(value)


def compare_json(
    expected: JsonValue, actual: JsonValue, path: str, errors: list[str], missing: list[str]
) -> int:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            errors.append(path)
            return 0
        matched: int = 0
        for key in sorted(expected):
            child_path: str = f"{path}.{key}"
            if key not in actual:
                missing.append(child_path)
            else:
                matched += compare_json(expected[key], actual[key], child_path, errors, missing)
        for key in sorted(set(actual) - set(expected)):
            errors.append(f"{path}.{key}")
        return matched
    if isinstance(expected, list):
        if not isinstance(actual, list):
            errors.append(path)
            return 0
        matched = 0
        common_length: int = min(len(expected), len(actual))
        for index in range(common_length):
            matched += compare_json(expected[index], actual[index], f"{path}[{index}]", errors, missing)
        for index in range(common_length, len(expected)):
            missing.append(f"{path}[{index}]")
        for index in range(common_length, len(actual)):
            errors.append(f"{path}[{index}]")
        return matched
    if expected != actual:
        errors.append(path)
        return 0
    return 1


def is_empty_json(value: JsonValue) -> bool:
    return (
        value is None
        or (isinstance(value, str) and not value.strip())
        or (isinstance(value, list | dict) and not value)
    )
