"""Recorded gesture specifications and event data."""

from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True)
class ParsedEvent:
    time: float | None
    device_path: str | None
    type: int
    code: int
    value: int
    delay_seconds: float
    delay_ms: int
    at_seconds: float | None


class ReplayFrame(TypedDict):
    delay_seconds: float
    events: list[ParsedEvent]


@dataclass(frozen=True)
class GestureSpec:
    name: str
    recordings: tuple[str, ...]
    common: bool


@dataclass(frozen=True)
class ReplayTarget:
    path: str
    x: int
    y: int
