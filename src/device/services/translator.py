"""Translate getevent action logs into PIAR replay packets."""

from __future__ import annotations

import re
import struct
from collections.abc import Iterable
from dataclasses import replace
from typing import Final

from src.device.types.gestures import ParsedEvent, ReplayFrame

EVENT_TYPES: Final[dict[str, int]] = {
    "EV_SYN": 0,
    "EV_KEY": 1,
    "EV_REL": 2,
    "EV_ABS": 3,
    "EV_MSC": 4,
    "EV_SW": 5,
}

EVENT_CODES: Final[dict[str, int]] = {
    "SYN_REPORT": 0,
    "SYN_MT_REPORT": 2,
    "BTN_TOUCH": 330,
    "BTN_TOOL_FINGER": 325,
    "ABS_X": 0,
    "ABS_Y": 1,
    "ABS_MT_SLOT": 47,
    "ABS_MT_TOUCH_MAJOR": 48,
    "ABS_MT_TOUCH_MINOR": 49,
    "ABS_MT_WIDTH_MAJOR": 50,
    "ABS_MT_WIDTH_MINOR": 51,
    "ABS_MT_ORIENTATION": 52,
    "ABS_MT_POSITION_X": 53,
    "ABS_MT_POSITION_Y": 54,
    "ABS_MT_TOOL_TYPE": 55,
    "ABS_MT_BLOB_ID": 56,
    "ABS_MT_TRACKING_ID": 57,
    "ABS_MT_PRESSURE": 58,
}


def parse_action_log(log_text: str) -> list[ParsedEvent]:
    raw_events: list[ParsedEvent] = [
        event
        for event in (parse_getevent_line(line) for line in str(log_text or "").splitlines())
        if event is not None
    ]
    first_timed_event: ParsedEvent | None = next(
        (event for event in raw_events if event.time is not None), None
    )
    base_time: float | None = first_timed_event.time if first_timed_event else None

    parsed: list[ParsedEvent] = []
    for index, event in enumerate(raw_events):
        previous: ParsedEvent | None = raw_events[index - 1] if index > 0 else None
        elapsed_seconds: float = (
            max(0.0, event.time - previous.time)
            if previous and event.time is not None and previous.time is not None
            else 0.0
        )
        delay_seconds: float = elapsed_seconds
        delay_ms: int = round(delay_seconds * 1000)
        at_seconds: float | None = (
            None if base_time is None or event.time is None else max(0.0, event.time - base_time)
        )
        parsed.append(
            replace(
                event,
                delay_seconds=delay_seconds,
                delay_ms=delay_ms,
                at_seconds=at_seconds,
            )
        )
    return parsed


def parse_getevent_line(line: str) -> ParsedEvent | None:
    text: str = str(line or "").strip()
    if not text:
        return None

    match: re.Match[str] | None = re.match(
        r"^(?:\[\s*(\d+(?:\.\d+)?)\]\s+)?([^:\s]+):\s+(\S+)\s+(\S+)\s+(\S+)", text
    ) or re.match(r"^(?:\[\s*(\d+(?:\.\d+)?)\]\s+)?(\S+)\s+(\S+)\s+(\S+)", text)
    if not match:
        return None

    groups: tuple[str | None, ...] = match.groups()
    has_device_path: bool = len(groups) == 5
    time_text: str | None = groups[0]
    device_path: str | None = groups[1] if has_device_path else None
    type_text: str | None = groups[2] if has_device_path else groups[1]
    code_text: str | None = groups[3] if has_device_path else groups[2]
    value_text: str | None = groups[4] if has_device_path else groups[3]

    if type_text is None or code_text is None or value_text is None:
        return None
    event_type: int | None = parse_type(type_text)
    event_code: int | None = parse_code(code_text)
    value: int | None = parse_getevent_number(value_text, signed=True)
    if event_type is None or event_code is None or value is None:
        return None

    return ParsedEvent(
        time=None if time_text is None else float(time_text),
        device_path=device_path,
        type=event_type,
        code=event_code,
        value=value,
        delay_seconds=0.0,
        delay_ms=0,
        at_seconds=None,
    )


def parse_type(token: str) -> int | None:
    return EVENT_TYPES.get(token) if token in EVENT_TYPES else parse_getevent_number(token)


def parse_code(token: str) -> int | None:
    return EVENT_CODES.get(token) if token in EVENT_CODES else parse_getevent_number(token)


def parse_getevent_number(token: str, signed: bool = False) -> int | None:
    text: str = str(token or "").strip()
    if text == "DOWN":
        return 1
    if text == "UP":
        return 0

    normalized: str = re.sub(r"^0x", "", text, flags=re.IGNORECASE)
    should_parse_as_hex: bool = bool(re.match(r"^-?0x[0-9a-f]+$", text, flags=re.IGNORECASE))
    if re.match(r"^[0-9a-f]+$", normalized, flags=re.IGNORECASE):
        should_parse_as_hex = should_parse_as_hex or (
            bool(re.search(r"[a-f]", normalized, flags=re.IGNORECASE))
            or len(normalized) in {4, 8}
            or bool(re.match(r"^0[0-9]+$", normalized))
        )

    if should_parse_as_hex:
        sign: int = -1 if normalized.startswith("-") else 1
        digits: str = normalized[1:] if normalized.startswith("-") else normalized
        value: int = sign * int(digits, 16)
        if signed and sign > 0 and len(digits) == 8 and value > 0x7FFFFFFF:
            return value - 0x100000000
        return value

    if re.match(r"^-?\d+$", text):
        return int(text)
    return None


def normalize_events_to_origin(events: Iterable[ParsedEvent]) -> list[ParsedEvent]:
    """Move the recorded action's first touch coordinate to ``(0, 0)``."""

    event_list: list[ParsedEvent] = list(events)
    origin_x: int | None = next((event.value for event in event_list if _is_x_coordinate_event(event)), None)
    origin_y: int | None = next((event.value for event in event_list if _is_y_coordinate_event(event)), None)
    if origin_x is None and origin_y is None:
        return event_list

    normalized: list[ParsedEvent] = []
    for event in event_list:
        if origin_x is not None and _is_x_coordinate_event(event):
            normalized.append(replace(event, value=event.value - origin_x))
        elif origin_y is not None and _is_y_coordinate_event(event):
            normalized.append(replace(event, value=event.value - origin_y))
        else:
            normalized.append(event)
    return normalized


def group_events_by_syn_report(events: Iterable[ParsedEvent]) -> list[ReplayFrame]:
    frames: list[ReplayFrame] = []
    current: list[ParsedEvent] = []
    last_syn_at_seconds: float | None = None

    for event in events:
        current.append(event)
        if event.type == EVENT_TYPES["EV_SYN"] and event.code == EVENT_CODES["SYN_REPORT"]:
            syn_at_seconds: float | None = event.at_seconds
            delay_seconds: float = (
                0.0
                if last_syn_at_seconds is None or syn_at_seconds is None
                else max(0.0, syn_at_seconds - last_syn_at_seconds)
            )
            frames.append({"delay_seconds": delay_seconds, "events": current})
            current = []
            last_syn_at_seconds = syn_at_seconds

    if current:
        first_at_seconds: float | None = next(
            (event.at_seconds for event in current if event.at_seconds is not None), None
        )
        delay_seconds = (
            0.0
            if last_syn_at_seconds is None or first_at_seconds is None
            else max(0.0, first_at_seconds - last_syn_at_seconds)
        )
        frames.append({"delay_seconds": delay_seconds, "events": current})
    return frames


def build_replay_packet(events: Iterable[ParsedEvent]) -> bytes:
    chunks: list[bytes] = [b"PIAR1\0\0\0"]
    for frame in group_events_by_syn_report(events):
        frame_events: list[ParsedEvent] = list(frame["events"])
        delay_us: int = max(0, min(0xFFFFFFFF, round(float(frame["delay_seconds"]) * 1_000_000)))
        chunks.append(struct.pack("<II", delay_us, len(frame_events)))
        body: bytearray = bytearray()
        for event in frame_events:
            body.extend(struct.pack("<HHi", event.type, event.code, event.value))
        chunks.append(bytes(body))
    return b"".join(chunks)


def _is_x_coordinate_event(event: ParsedEvent) -> bool:
    return event.type == EVENT_TYPES["EV_ABS"] and event.code in {
        EVENT_CODES["ABS_MT_POSITION_X"],
        EVENT_CODES["ABS_X"],
    }


def _is_y_coordinate_event(event: ParsedEvent) -> bool:
    return event.type == EVENT_TYPES["EV_ABS"] and event.code in {
        EVENT_CODES["ABS_MT_POSITION_Y"],
        EVENT_CODES["ABS_Y"],
    }
