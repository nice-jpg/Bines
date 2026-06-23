"""Shadow recording session state and device operation logic."""

from __future__ import annotations

from datetime import datetime, timezone
import subprocess
import threading
import time
from typing import Any

from .adb import AdbClient
from .config import SCHEMA_VERSION, ShadowConfig
from .display import DisplayStreamer, create_display_streamer
from .input_stream import InputCapabilities, InputStreamInjector, TouchEventEncoder, packet_to_base64, parse_input_capabilities


class ShadowSession:
    def __init__(
        self,
        config: ShadowConfig | None = None,
        *,
        adb: AdbClient | None = None,
        display_streamer: DisplayStreamer | None = None,
        input_injector: InputStreamInjector | None = None,
    ) -> None:
        self.config = config or ShadowConfig.from_env()
        self.adb = adb or AdbClient(self.config)
        self.display_streamer = display_streamer or create_display_streamer(self.adb, self.config)
        self.input_injector = input_injector or InputStreamInjector(self.adb, self.config)
        self.screen_width = 0
        self.screen_height = 0
        self.device_info: dict[str, str] = {}
        self.operations: list[dict[str, Any]] = []
        self.raw_browser_events: list[dict[str, Any]] = []
        self._piar_body = bytearray()
        self.input_capabilities: InputCapabilities | None = None
        self.touch_encoder: TouchEventEncoder | None = None
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
        self.adb.ensure_input_stream_helper(self.config.input_stream_helper)
        try:
            capabilities_text = self.adb.input_capabilities(self.config.input_device)
            self.input_capabilities = parse_input_capabilities(capabilities_text, (self.screen_width, self.screen_height))
        except Exception:
            self.input_capabilities = InputCapabilities.default_for_screen(self.screen_width, self.screen_height)
        self.touch_encoder = TouchEventEncoder(self.input_capabilities, (self.screen_width, self.screen_height))
        self.display_streamer.start()

    def frame_png(self) -> bytes:
        return self.display_streamer.latest_frame(timeout=2)

    def mjpeg_frames(self):
        return self.display_streamer.mjpeg_frames()

    def start_recording(self) -> dict[str, Any]:
        with self._lock:
            if self.is_recording:
                return {"ok": True, "recording": True, "started_at": self._recording_started_at}
            if self.screen_width <= 0 or self.screen_height <= 0:
                self.prepare()
            self.operations = []
            self.raw_browser_events = []
            self._piar_body = bytearray()
            capabilities = self.input_capabilities or InputCapabilities.default_for_screen(self.screen_width, self.screen_height)
            self.touch_encoder = TouchEventEncoder(capabilities, (self.screen_width, self.screen_height))
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
        return self.handle_pointer_batch({"events": [payload]})

    def handle_pointer_batch(self, payload: dict[str, Any]) -> dict[str, Any]:
        events = payload.get("events") or []
        if not isinstance(events, list):
            return {"ok": False, "error": "events must be a list"}
        if self.touch_encoder is None:
            if self.screen_width <= 0 or self.screen_height <= 0:
                self.prepare()
            capabilities = self.input_capabilities or InputCapabilities.default_for_screen(self.screen_width, self.screen_height)
            self.touch_encoder = TouchEventEncoder(capabilities, (self.screen_width, self.screen_height))

        packet_body, audits = self.touch_encoder.encode_batch([event for event in events if isinstance(event, dict)])
        self.input_injector.write_frames(packet_body)
        self._record_low_level_input(packet_body, audits)
        return {"ok": True, "events": len(events), "frames_bytes": len(packet_body), "accepted": len(audits)}

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
            "raw_browser_events": list(self.raw_browser_events),
            "piar_base64": packet_to_base64(bytes(self._piar_body)) if self._piar_body else "",
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

    def _record_low_level_input(self, packet_body: bytes, audits: list[dict[str, Any]]) -> None:
        if not self.is_recording:
            return
        self._piar_body.extend(packet_body)
        self.raw_browser_events.extend(audits)
        self.operations.extend(audits)

    def close(self) -> None:
        if self.is_recording:
            self.stop_recording()
        self.input_injector.stop()
        self.display_streamer.stop()


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
