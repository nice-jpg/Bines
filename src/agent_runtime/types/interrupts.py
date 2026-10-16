"""Normalize LangGraph interrupts at the framework boundary."""

from collections.abc import Mapping, Sequence

from langgraph.types import Interrupt

from src.serialization.json import JsonValue, json_value
from src.serialization.validation import is_value_sequence

from .execution import InterruptKind


def extract_interrupts(state: Mapping[str, object]) -> list[Interrupt | JsonValue]:
    raw: object = state.get("__interrupt__") or state.get("interrupts")
    if raw is None:
        return []
    values: Sequence[object] = raw if is_value_sequence(raw) else [raw]
    interrupts: list[Interrupt | JsonValue] = []
    for value in values:
        interrupts.append(value if isinstance(value, Interrupt) else json_value(value))
    return interrupts


def _interrupt_value(value: Interrupt | JsonValue) -> JsonValue:
    return json_value(value.value) if isinstance(value, Interrupt) else value


def interrupt_kind(interrupts: Sequence[Interrupt | JsonValue]) -> InterruptKind:
    for item in interrupts:
        value: JsonValue = _interrupt_value(item)
        if isinstance(value, dict):
            kind: JsonValue = value.get("kind")
            if isinstance(kind, str) and kind == "liveness":
                return InterruptKind.LIVENESS
    return InterruptKind.HUMAN


def interrupt_output(interrupts: Sequence[Interrupt | JsonValue]) -> str:
    for item in interrupts:
        value: JsonValue = _interrupt_value(item)
        if isinstance(value, dict):
            message: JsonValue = value.get("message")
            if isinstance(message, str):
                return message
    return ""
