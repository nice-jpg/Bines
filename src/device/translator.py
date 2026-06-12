"""Translate getevent action logs into PIAR replay packets."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import re
import struct
import tempfile
from typing import Iterable

try:
    from .adapter import AndroidDevice, DEFAULT_ACTION_DIR
except ImportError:  # Supports direct PYTHONPATH=src imports.
    from device.adapter import AndroidDevice, DEFAULT_ACTION_DIR


EVENT_TYPES = {
    "EV_SYN": 0,
    "EV_KEY": 1,
    "EV_REL": 2,
    "EV_ABS": 3,
    "EV_MSC": 4,
    "EV_SW": 5,
}

EVENT_CODES = {
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


def check_actions(
    device: AndroidDevice,
    local_dir: str = "src/device/actions",
    device_dir: str = DEFAULT_ACTION_DIR,
) -> list[str]:
    local_path = Path(local_dir)
    if not local_path.exists():
        return []

    supported_actions = set(device.get_supported_actions(device_dir))
    translated: list[str] = []
    for action_path in sorted(path for path in local_path.iterdir() if path.is_file()):
        if action_path.name in supported_actions:
            continue
        translate(str(action_path), device, device_dir=device_dir)
        translated.append(action_path.name)
    return translated


def translate(
    action_path: str,
    device: AndroidDevice,
    device_dir: str = DEFAULT_ACTION_DIR,
) -> str:
    source_path = Path(action_path)
    log_text = source_path.read_text(encoding="utf-8")
    events = parse_action_log(log_text)
    packet = build_replay_packet(events)

    with tempfile.TemporaryDirectory(prefix="bines-action-") as tmp_dir:
        staged_path = Path(tmp_dir) / source_path.name
        staged_path.write_bytes(packet)
        return device.add_action(str(staged_path), device_dir=device_dir)


def parse_action_log(log_text: str, time_scale: float = 1, max_delay_ms: int | None = None) -> list[ParsedEvent]:
    raw_events = [
        event
        for event in (parse_getevent_line(line) for line in str(log_text or "").splitlines())
        if event is not None
    ]
    first_timed_event = next((event for event in raw_events if event.time is not None), None)
    base_time = first_timed_event.time if first_timed_event else None

    parsed: list[ParsedEvent] = []
    for index, event in enumerate(raw_events):
        previous = raw_events[index - 1] if index > 0 else None
        elapsed_seconds = (
            max(0.0, event.time - previous.time)
            if previous and event.time is not None and previous.time is not None
            else 0.0
        )
        delay_seconds = _clamp_delay_seconds(elapsed_seconds * time_scale, max_delay_ms)
        delay_ms = _clamp_delay_ms(round(delay_seconds * 1000), max_delay_ms)
        at_seconds = (
            None
            if base_time is None or event.time is None
            else max(0.0, (event.time - base_time) * time_scale)
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
    text = str(line or "").strip()
    if not text:
        return None

    match = (
        re.match(r"^(?:\[\s*(\d+(?:\.\d+)?)\]\s+)?([^:\s]+):\s+(\S+)\s+(\S+)\s+(\S+)", text)
        or re.match(r"^(?:\[\s*(\d+(?:\.\d+)?)\]\s+)?(\S+)\s+(\S+)\s+(\S+)", text)
    )
    if not match:
        return None

    groups = match.groups()
    has_device_path = len(groups) == 5
    time_text = groups[0]
    device_path = groups[1] if has_device_path else None
    type_text = groups[2] if has_device_path else groups[1]
    code_text = groups[3] if has_device_path else groups[2]
    value_text = groups[4] if has_device_path else groups[3]

    event_type = parse_type(type_text)
    event_code = parse_code(code_text)
    value = parse_getevent_number(value_text, signed=True)
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
    text = str(token or "").strip()
    if text == "DOWN":
        return 1
    if text == "UP":
        return 0

    normalized = re.sub(r"^0x", "", text, flags=re.IGNORECASE)
    should_parse_as_hex = bool(re.match(r"^-?0x[0-9a-f]+$", text, flags=re.IGNORECASE))
    if re.match(r"^[0-9a-f]+$", normalized, flags=re.IGNORECASE):
        should_parse_as_hex = should_parse_as_hex or (
            bool(re.search(r"[a-f]", normalized, flags=re.IGNORECASE))
            or len(normalized) in {4, 8}
            or bool(re.match(r"^0[0-9]+$", normalized))
        )

    if should_parse_as_hex:
        sign = -1 if normalized.startswith("-") else 1
        digits = normalized[1:] if normalized.startswith("-") else normalized
        value = sign * int(digits, 16)
        if signed and sign > 0 and len(digits) == 8 and value > 0x7FFFFFFF:
            return value - 0x100000000
        return value

    if re.match(r"^-?\d+$", text):
        return int(text)
    return None


def group_events_by_syn_report(events: Iterable[ParsedEvent]) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    current: list[ParsedEvent] = []
    last_syn_at_seconds: float | None = None

    for event in events:
        current.append(event)
        if event.type == EVENT_TYPES["EV_SYN"] and event.code == EVENT_CODES["SYN_REPORT"]:
            syn_at_seconds = event.at_seconds
            delay_seconds = (
                0.0
                if last_syn_at_seconds is None or syn_at_seconds is None
                else max(0.0, syn_at_seconds - last_syn_at_seconds)
            )
            frames.append({"delay_seconds": delay_seconds, "events": current})
            current = []
            last_syn_at_seconds = syn_at_seconds

    if current:
        first_at_seconds = next((event.at_seconds for event in current if event.at_seconds is not None), None)
        delay_seconds = (
            0.0
            if last_syn_at_seconds is None or first_at_seconds is None
            else max(0.0, first_at_seconds - last_syn_at_seconds)
        )
        frames.append({"delay_seconds": delay_seconds, "events": current})
    return frames


def build_replay_packet(events: Iterable[ParsedEvent]) -> bytes:
    chunks = [b"PIAR1\0\0\0"]
    for frame in group_events_by_syn_report(events):
        frame_events = list(frame["events"])
        delay_us = max(0, min(0xFFFFFFFF, round(float(frame["delay_seconds"]) * 1_000_000)))
        chunks.append(struct.pack("<II", delay_us, len(frame_events)))
        body = bytearray()
        for event in frame_events:
            body.extend(struct.pack("<HHi", event.type, event.code, event.value))
        chunks.append(bytes(body))
    return b"".join(chunks)


def _clamp_delay_seconds(delay_seconds: float, max_delay_ms: int | None) -> float:
    if max_delay_ms is None:
        return delay_seconds
    return min(delay_seconds, max_delay_ms / 1000)


def _clamp_delay_ms(delay_ms: int, max_delay_ms: int | None) -> int:
    if max_delay_ms is None:
        return delay_ms
    return min(delay_ms, max_delay_ms)

