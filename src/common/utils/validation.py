"""Validation of untyped values supplied by framework extension points."""

from collections.abc import Mapping, Sequence
from typing import TypeGuard


def is_string_mapping(value: object) -> TypeGuard[Mapping[str, object]]:
    if not _is_mapping(value):
        return False
    return all(isinstance(key, str) for key in value)


def _is_mapping(value: object) -> TypeGuard[Mapping[object, object]]:
    return isinstance(value, Mapping)


def is_value_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, list | tuple)
