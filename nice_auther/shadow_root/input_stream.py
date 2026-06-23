"""High-fidelity browser pointer event encoding and streaming injection."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import re
import struct
import subprocess
import threading
from typing import Any

from .adb import AdbClient
from .config import ShadowConfig


EV_SYN = 0
EV_KEY = 1
EV_ABS = 3
SYN_REPORT = 0
BTN_TOUCH = 330
ABS_X = 0
ABS_Y = 1
ABS_MT_SLOT = 47
ABS_MT_POSITION_X = 53
ABS_MT_POSITION_Y = 54
ABS_MT_TRACKING_ID = 57
ABS_MT_PRESSURE = 58
PIAR_MAGIC = b"PIAR1\0\0\0"


@dataclass(frozen=True)
class AbsRange:
    minimum: int
    maximum: int

    def clamp(self, value: int) -> int:
        return max(self.minimum, min(self.maximum, int(value)))


@dataclass(frozen=True)
class InputCapabilities:
    x: AbsRange
    y: AbsRange
    supports_slots: bool = False

    @classmethod
    def default_for_screen(cls, width: int, height: int) -> "InputCapabilities":
        return cls(AbsRange(0, max(0, width - 1)), AbsRange(0, max(0, height - 1)), supports_slots=True)


class TouchEventEncoder:
    def __init__(self, capabilities: InputCapabilities, screen_size: tuple[int, int]) -> None:
        self.capabilities = capabilities
        self.screen_width = max(1, int(screen_size[0]))
        self.screen_height = max(1, int(screen_size[1]))
        self._tracking_id = 1
        self._active: dict[int, int] = {}
        self._last_client_time_ms: float | None = None

    def encode_batch(self, events: list[dict[str, Any]]) -> tuple[bytes, list[dict[str, Any]]]:
        packet = bytearray()
        audits: list[dict[str, Any]] = []
        for event in events:
            frame, audit = self.encode_event(event)
            if frame:
                packet.extend(frame)
                audits.append(audit)
        return bytes(packet), audits

    def encode_event(self, event: dict[str, Any]) -> tuple[bytes, dict[str, Any]]:
        event_type = str(event.get("type") or event.get("event") or "").strip().lower()
        pointer_id = _normalize_pointer_id(event.get("pointer_id", event.get("pointerId", 1)))
        raw_x, raw_y = self.map_client_point(event)
        delay_us = self._delay_us(event.get("client_time_ms"))
        events: list[tuple[int, int, int]] = []

        if self.capabilities.supports_slots:
            events.append((EV_ABS, ABS_MT_SLOT, 0))

        if event_type in {"pointerdown", "down"}:
            tracking_id = self._tracking_id
            self._tracking_id += 1
            self._active[pointer_id] = tracking_id
            events.extend(
                [
                    (EV_ABS, ABS_MT_TRACKING_ID, tracking_id),
                    (EV_ABS, ABS_MT_POSITION_X, raw_x),
                    (EV_ABS, ABS_MT_POSITION_Y, raw_y),
                    (EV_ABS, ABS_MT_PRESSURE, _normalize_pressure(event.get("pressure"))),
                    (EV_KEY, BTN_TOUCH, 1),
                    (EV_SYN, SYN_REPORT, 0),
                ]
            )
        elif event_type in {"pointermove", "move"}:
            if pointer_id not in self._active:
                return b"", self._audit(event_type, pointer_id, event, raw_x, raw_y, skipped=True)
            events.extend(
                [
                    (EV_ABS, ABS_MT_POSITION_X, raw_x),
                    (EV_ABS, ABS_MT_POSITION_Y, raw_y),
                    (EV_SYN, SYN_REPORT, 0),
                ]
            )
        elif event_type in {"pointerup", "up", "pointercancel", "cancel"}:
            if pointer_id not in self._active:
                return b"", self._audit(event_type, pointer_id, event, raw_x, raw_y, skipped=True)
            self._active.pop(pointer_id, None)
            events.extend(
                [
                    (EV_ABS, ABS_MT_POSITION_X, raw_x),
                    (EV_ABS, ABS_MT_POSITION_Y, raw_y),
                    (EV_ABS, ABS_MT_TRACKING_ID, -1),
                    (EV_KEY, BTN_TOUCH, 0),
                    (EV_SYN, SYN_REPORT, 0),
                ]
            )
        else:
            return b"", self._audit(event_type, pointer_id, event, raw_x, raw_y, skipped=True)

        return _build_frame(delay_us, events), self._audit(event_type, pointer_id, event, raw_x, raw_y)

    def map_client_point(self, event: dict[str, Any]) -> tuple[int, int]:
        client_width = max(1.0, float(event.get("width") or self.screen_width))
        client_height = max(1.0, float(event.get("height") or self.screen_height))
        client_x = max(0.0, min(client_width, float(event.get("x", 0))))
        client_y = max(0.0, min(client_height, float(event.get("y", 0))))
        raw_x = round(self.capabilities.x.minimum + client_x * (self.capabilities.x.maximum - self.capabilities.x.minimum) / client_width)
        raw_y = round(self.capabilities.y.minimum + client_y * (self.capabilities.y.maximum - self.capabilities.y.minimum) / client_height)
        return self.capabilities.x.clamp(raw_x), self.capabilities.y.clamp(raw_y)

    def _delay_us(self, client_time_ms: object) -> int:
        if client_time_ms is None:
            return 0
        current = float(client_time_ms)
        if self._last_client_time_ms is None:
            self._last_client_time_ms = current
            return 0
        delta_ms = max(0.0, min(250.0, current - self._last_client_time_ms))
        self._last_client_time_ms = current
        return int(delta_ms * 1000)

    def _audit(
        self,
        event_type: str,
        pointer_id: int,
        event: dict[str, Any],
        raw_x: int,
        raw_y: int,
        *,
        skipped: bool = False,
    ) -> dict[str, Any]:
        return {
            "type": event_type,
            "pointer_id": pointer_id,
            "client": {
                "x": event.get("x"),
                "y": event.get("y"),
                "width": event.get("width"),
                "height": event.get("height"),
                "client_time_ms": event.get("client_time_ms"),
            },
            "device": {"x": raw_x, "y": raw_y},
            "skipped": skipped,
        }


class InputStreamInjector:
    def __init__(self, adb: AdbClient, config: ShadowConfig) -> None:
        self.adb = adb
        self.config = config
        self.process: subprocess.Popen[bytes] | None = None
        self._lock = threading.RLock()
        self._started = False
        self.writes: list[bytes] = []

    def write_frames(self, frames: bytes) -> dict[str, Any]:
        if not frames:
            return self.status(extra={"written_bytes": 0})
        with self._lock:
            self._ensure_started()
            assert self.process is not None and self.process.stdin is not None
            returncode = self.process.poll()
            if returncode is not None:
                return self.status(extra={"ok": False, "returncode": returncode, "written_bytes": 0})
            self.process.stdin.write(frames)
            self.process.stdin.flush()
            self.writes.append(frames)
            return self.status(extra={"written_bytes": len(frames)})

    def status(self, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        process = self.process
        status = {
            "ok": True,
            "started": self._started,
            "pid": getattr(process, "pid", None) if process is not None else None,
            "returncode": process.poll() if process is not None else None,
            "writes": len(self.writes),
            "total_written_bytes": sum(len(chunk) for chunk in self.writes),
        }
        if extra:
            status.update(extra)
            if extra.get("ok") is False:
                status["ok"] = False
        return status

    def stop(self) -> None:
        with self._lock:
            process = self.process
            self.process = None
        if process is None:
            return
        if process.stdin:
            try:
                process.stdin.close()
            except Exception:
                pass
        try:
            process.wait(timeout=2)
        except Exception:
            process.kill()

    def _ensure_started(self) -> None:
        if self.process is None:
            self.process = self.adb.start_input_stream(self.config.input_device, self.config.input_stream_helper)
            self._started = False
        if not self._started:
            assert self.process.stdin is not None
            self.process.stdin.write(PIAR_MAGIC)
            self.process.stdin.flush()
            self._started = True


def parse_input_capabilities(text: str, screen_size: tuple[int, int]) -> InputCapabilities:
    ranges: dict[int, AbsRange] = {}
    current_code: int | None = None
    supports_slots = False
    for line in str(text or "").splitlines():
        if "ABS_MT_SLOT" in line:
            supports_slots = True
        if "ABS_MT_POSITION_X" in line or re.search(r"\bABS_X\b", line):
            current_code = ABS_MT_POSITION_X
        elif "ABS_MT_POSITION_Y" in line or re.search(r"\bABS_Y\b", line):
            current_code = ABS_MT_POSITION_Y
        elif "ABS_" in line:
            current_code = None

        range_match = re.search(r"min\s+(-?\d+),\s+max\s+(-?\d+)", line)
        if range_match and current_code is not None:
            ranges[current_code] = AbsRange(int(range_match.group(1)), int(range_match.group(2)))
            current_code = None

    fallback = InputCapabilities.default_for_screen(*screen_size)
    return InputCapabilities(
        ranges.get(ABS_MT_POSITION_X, fallback.x),
        ranges.get(ABS_MT_POSITION_Y, fallback.y),
        supports_slots=supports_slots,
    )


def packet_to_base64(packet_body: bytes) -> str:
    return base64.b64encode(PIAR_MAGIC + packet_body).decode("ascii")


def _build_frame(delay_us: int, events: list[tuple[int, int, int]]) -> bytes:
    body = bytearray(struct.pack("<II", max(0, int(delay_us)), len(events)))
    for event_type, code, value in events:
        body.extend(struct.pack("<HHi", event_type, code, int(value)))
    return bytes(body)


def _normalize_pointer_id(value: object) -> int:
    try:
        pointer_id = int(value)
    except (TypeError, ValueError):
        return 1
    return abs(pointer_id) or 1


def _normalize_pressure(value: object) -> int:
    if value is None:
        return 50
    try:
        pressure = float(value)
    except (TypeError, ValueError):
        return 50
    if pressure <= 0:
        return 50
    if pressure <= 1:
        return max(1, round(pressure * 100))
    return max(1, round(pressure))
