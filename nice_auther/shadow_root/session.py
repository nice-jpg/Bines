"""Shadow recording session state and device operation logic."""

from __future__ import annotations

from datetime import datetime, timezone
import subprocess
import threading
import time
from typing import Any

from .adb import AdbClient
from .config import SCHEMA_VERSION, ShadowConfig


class ShadowSession:
    def __init__(self, config: ShadowConfig | None = None, *, adb: AdbClient | None = None) -> None:
        self.config = config or ShadowConfig.from_env()
        self.adb = adb or AdbClient(self.config)
        self.screen_width = 0
        self.screen_height = 0
        self.device_info: dict[str, str] = {}
        self.operations: list[dict[str, Any]] = []
        self._recording_process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._raw_events: list[str] = []
        self._recording_started_at = ""
        self._pointer_down: dict[int, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @property
    def is_recording(self) -> bool:
        return self._recording_process is not None

    def prepare(self) -> None:
        self.screen_width, self.screen_height = self.adb.screen_size()
        self.device_info = self.adb.device_info()

    def frame_png(self) -> bytes:
        return self.adb.screencap_png()

    def start_recording(self) -> dict[str, Any]:
        with self._lock:
            if self.is_recording:
                return {"ok": True, "recording": True, "started_at": self._recording_started_at}
            if self.screen_width <= 0 or self.screen_height <= 0:
                self.prepare()
            self.operations = []
            self._raw_events = []
            self._recording_started_at = _utc_now()
            self._recording_process = self.adb.start_getevent(self.config.input_device)
            self._reader_thread = threading.Thread(target=self._read_getevent_output, daemon=True)
            self._reader_thread.start()
            return {"ok": True, "recording": True, "started_at": self._recording_started_at}

    def stop_recording(self) -> dict[str, Any]:
        with self._lock:
            process = self._recording_process
            if process is None:
                return self.build_bundle()
            self._recording_process = None
            process.terminate()
        try:
            process.wait(timeout=2)
        except Exception:
            process.kill()
            process.wait(timeout=2)
        if self._reader_thread:
            self._reader_thread.join(timeout=2)
        return self.build_bundle()

    def handle_pointer_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_type = str(payload.get("type") or payload.get("event") or "").strip()
        pointer_id = int(payload.get("pointer_id", 1))
        x, y = self.map_client_point(payload)
        now = time.monotonic()
        audit = {
            "type": event_type,
            "client": {
                "x": payload.get("x"),
                "y": payload.get("y"),
                "width": payload.get("width"),
                "height": payload.get("height"),
            },
            "device": {"x": x, "y": y},
            "at": _utc_now(),
        }

        if event_type in {"pointerdown", "down"}:
            self._pointer_down[pointer_id] = {"x": x, "y": y, "at": now}
            self._record_operation(audit)
            return {"ok": True, "action": "down", "x": x, "y": y}

        if event_type in {"pointerup", "up", "click"}:
            start = self._pointer_down.pop(pointer_id, {"x": x, "y": y, "at": now})
            duration_ms = int(max(1, (now - float(start["at"])) * 1000))
            dx = x - int(start["x"])
            dy = y - int(start["y"])
            if abs(dx) <= 8 and abs(dy) <= 8 and duration_ms < 500:
                self.adb.inject_tap(x, y)
                audit["action"] = "tap"
            else:
                self.adb.inject_swipe((int(start["x"]), int(start["y"])), (x, y), duration_ms)
                audit["action"] = "swipe"
                audit["duration_ms"] = duration_ms
            self._record_operation(audit)
            return {"ok": True, "action": audit["action"], "x": x, "y": y}

        if event_type in {"longpress", "long_press"}:
            duration_ms = int(payload.get("duration_ms", 600))
            self.adb.inject_swipe((x, y), (x, y), duration_ms)
            audit["action"] = "long_press"
            audit["duration_ms"] = duration_ms
            self._record_operation(audit)
            return {"ok": True, "action": "long_press", "x": x, "y": y}

        return {"ok": False, "error": f"unsupported pointer event: {event_type}"}

    def map_client_point(self, payload: dict[str, Any]) -> tuple[int, int]:
        if self.screen_width <= 0 or self.screen_height <= 0:
            self.prepare()
        client_width = max(1.0, float(payload.get("width") or self.screen_width))
        client_height = max(1.0, float(payload.get("height") or self.screen_height))
        client_x = float(payload.get("x", 0))
        client_y = float(payload.get("y", 0))
        x = round(client_x * self.screen_width / client_width)
        y = round(client_y * self.screen_height / client_height)
        return _clamp(x, 0, self.screen_width - 1), _clamp(y, 0, self.screen_height - 1)

    def build_bundle(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": _utc_now(),
            "recording_started_at": self._recording_started_at,
            "input_device": self.config.input_device,
            "screen": {"width": self.screen_width, "height": self.screen_height},
            "device_info": dict(self.device_info),
            "raw_getevent_log": "".join(self._raw_events),
            "operations": list(self.operations),
        }

    def _read_getevent_output(self) -> None:
        process = self._recording_process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self._raw_events.append(line)

    def _record_operation(self, operation: dict[str, Any]) -> None:
        if self.is_recording:
            self.operations.append(operation)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

