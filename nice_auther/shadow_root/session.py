"""Shadow recording session state and device operation logic."""

from __future__ import annotations

from datetime import datetime, timezone
import subprocess
import threading
import time
from typing import Any

from .adb import AdbClient
from .android_agent import AndroidShadowAgent
from .config import SCHEMA_VERSION, ShadowConfig
from .display import DisplayStreamer, create_display_streamer
from .input_stream import InputCapabilities, InputStreamInjector, TouchEventEncoder, packet_to_base64, parse_input_capabilities
from .process_log import log_event
from .reachability import ReachabilityResult, check_android_udp_reachability, log_reachability_result
from .webrtc import WebRtcGateway


class ShadowSession:
    def __init__(
        self,
        config: ShadowConfig | None = None,
        *,
        adb: AdbClient | None = None,
        display_streamer: DisplayStreamer | None = None,
        input_injector: InputStreamInjector | None = None,
        android_agent: AndroidShadowAgent | None = None,
        webrtc_gateway: WebRtcGateway | None = None,
    ) -> None:
        self.config = config or ShadowConfig.from_env()
        self.adb = adb or AdbClient(self.config)
        self.display_streamer = display_streamer or create_display_streamer(self.adb, self.config)
        self.input_injector = input_injector or InputStreamInjector(self.adb, self.config)
        self.android_agent = android_agent or (AndroidShadowAgent(self.adb, self.config) if self.is_webrtc_backend else None)
        self.webrtc_gateway = webrtc_gateway or (WebRtcGateway(self.config) if self.is_webrtc_backend else None)
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
        self._webrtc_error = ""
        self._reachability: ReachabilityResult | None = None
        self._adb_reverse_ports: list[int] = []
        self._lock = threading.RLock()

    @property
    def is_recording(self) -> bool:
        return self._recording_process is not None

    @property
    def is_webrtc_backend(self) -> bool:
        return self.config.video_backend.strip().lower() in {"webrtc_h264", "scrcpy_h264"}

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
        if self.is_webrtc_backend:
            self._start_webrtc_stack()

    def frame_png(self) -> bytes:
        return self.display_streamer.latest_frame(timeout=2)

    def mjpeg_frames(self):
        return self.display_streamer.mjpeg_frames()

    def handle_webrtc_offer(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_webrtc_backend or self.webrtc_gateway is None:
            return {"ok": False, "error": "webrtc_h264 backend is not enabled"}
        if self._webrtc_error:
            return {"ok": False, "error": self._webrtc_error}
        if payload.get("type") != "offer" or not isinstance(payload.get("sdp"), str):
            return {"ok": False, "error": "payload must contain WebRTC offer sdp and type"}
        answer = self.webrtc_gateway.offer({"type": payload["type"], "sdp": payload["sdp"]})
        if "sdp" not in answer or "type" not in answer:
            return {
                "ok": False,
                "error": answer.get("error") or "WebRTC gateway returned an invalid answer",
                "gateway": answer,
            }
        return answer

    def wake_display(self) -> dict[str, Any]:
        control_port = self.config.webrtc_control_port or (self.config.port + 1002)
        actions: list[dict[str, Any]] = []
        for command in (
            ["input", "keyevent", "KEYCODE_WAKEUP"],
            ["input", "keyevent", "KEYCODE_MENU"],
        ):
            try:
                self.adb.shell(command, root=True)
                actions.append({"command": command, "ok": True})
            except Exception as exc:
                actions.append({"command": command, "ok": False, "error": str(exc)})
        try:
            self.adb.start_shell(
                "sh -c 'printf PLI | (toybox nc -u -w 1 127.0.0.1 "
                + str(control_port)
                + " || nc -u -w 1 127.0.0.1 "
                + str(control_port)
                + ")'",
                root=False,
            )
            actions.append({"command": "agent PLI", "ok": True, "port": control_port})
        except Exception as exc:
            actions.append({"command": "agent PLI", "ok": False, "port": control_port, "error": str(exc)})
        log_event("shadow_root.session", "wake display", actions=actions)
        return {"ok": any(action.get("ok") for action in actions), "actions": actions}

    def status(self) -> dict[str, Any]:
        transport = self.config.webrtc_transport
        effective_rtp_host = "127.0.0.1" if transport.strip().lower() == "adb_reverse_tcp" else self.config.webrtc_rtp_host
        payload: dict[str, Any] = {
            "ok": True,
            "recording": self.is_recording,
            "screen": {"width": self.screen_width, "height": self.screen_height},
            "frame_interval_ms": self.config.frame_interval_ms,
            "video": {
                "backend": self.config.video_backend,
                "format": self.config.video_format,
                "fps": self.config.video_fps,
                "quality": self.config.video_quality,
                "scale": self.config.video_scale,
                "max_size": self.config.video_max_size,
                "bitrate": self.config.video_bitrate,
                "i_frame_interval_ms": self.config.video_iframe_interval_ms,
                "transport": transport,
                "rtp_host": effective_rtp_host,
                "rtp_listen_host": self.config.webrtc_rtp_listen_host,
                "rtp_port": self.config.webrtc_rtp_port or (self.config.port + 1001),
            },
        }
        if self.android_agent is not None:
            payload["android_agent"] = self.android_agent.status()
        if self.webrtc_gateway is not None:
            payload["webrtc_gateway"] = self.webrtc_gateway.status()
        if self._reachability is not None:
            payload["reachability"] = self._reachability.to_dict()
        if self._webrtc_error:
            payload["webrtc_error"] = self._webrtc_error
        return payload

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
        injector_status = self.input_injector.write_frames(packet_body)
        self._record_low_level_input(packet_body, audits)
        return {
            "ok": injector_status.get("ok", True),
            "events": len(events),
            "frames_bytes": len(packet_body),
            "accepted": len(audits),
            "injector": injector_status,
        }

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

    def _start_webrtc_stack(self) -> None:
        self._webrtc_error = ""
        try:
            if self.android_agent is None or self.webrtc_gateway is None:
                raise RuntimeError("webrtc_h264 backend requires Android agent and WebRTC gateway")
            self.android_agent.validate_config()
            transport = self.config.webrtc_transport.strip().lower()
            if transport == "adb_reverse_tcp":
                self._setup_adb_reverse()
            elif transport == "udp_rtp":
                self._reachability = check_android_udp_reachability(self.adb, self.config)
                log_reachability_result(self._reachability)
            elif transport != "tcp_direct":
                raise RuntimeError(f"unsupported WebRTC transport: {transport}")
            self.webrtc_gateway.start()
            self.android_agent.start()
        except Exception as exc:
            self._webrtc_error = str(exc)
            if self.android_agent is not None:
                self.android_agent.stop()
            if self.webrtc_gateway is not None:
                self.webrtc_gateway.stop()
            self._cleanup_adb_reverse()

    def _setup_adb_reverse(self) -> None:
        rtp_port = self.config.webrtc_rtp_port or (self.config.port + 1001)
        log_event("shadow_root.adb_reverse", "setup start", device_port=rtp_port, host_port=rtp_port)
        self.adb.reverse_tcp(rtp_port, rtp_port)
        log_event("shadow_root.adb_reverse", "setup ok", device_port=rtp_port, host_port=rtp_port)
        self._adb_reverse_ports.append(rtp_port)

    def _cleanup_adb_reverse(self) -> None:
        while self._adb_reverse_ports:
            port = self._adb_reverse_ports.pop()
            log_event("shadow_root.adb_reverse", "remove", device_port=port)
            self.adb.remove_reverse_tcp(port)

    def close(self) -> None:
        if self.is_recording:
            self.stop_recording()
        if self.webrtc_gateway is not None:
            self.webrtc_gateway.stop()
        if self.android_agent is not None:
            self.android_agent.stop()
        self._cleanup_adb_reverse()
        self.input_injector.stop()
        self.display_streamer.stop()


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
