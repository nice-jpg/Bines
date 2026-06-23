"""Configuration for shadow_root."""

from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_INPUT_DEVICE = "/dev/input/event3"
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ShadowConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    adb_path: str = "adb"
    adb_serial: str = ""
    input_device: str = DEFAULT_INPUT_DEVICE
    frame_interval_ms: int = 500
    video_backend: str = "mjpeg_screencap"
    video_fps: int = 8
    video_max_in_flight: int = 1
    video_format: str = "jpeg"
    video_quality: int = 45
    video_scale: float = 0.6
    input_stream_helper: str = "/data/local/tmp/pi_input_stream"
    token: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ShadowConfig":
        source = os.environ if env is None else env
        return cls(
            host=source.get("SHADOW_HOST", "127.0.0.1"),
            port=int(source.get("SHADOW_PORT", "8765")),
            adb_path=source.get("SHADOW_ADB_PATH", "adb"),
            adb_serial=source.get("SHADOW_ADB_SERIAL", ""),
            input_device=source.get("SHADOW_INPUT_DEVICE", DEFAULT_INPUT_DEVICE),
            frame_interval_ms=int(source.get("SHADOW_FRAME_INTERVAL_MS", "500")),
            video_backend=source.get("SHADOW_VIDEO_BACKEND", "mjpeg_screencap"),
            video_fps=int(source.get("SHADOW_VIDEO_FPS", "8")),
            video_max_in_flight=int(source.get("SHADOW_VIDEO_MAX_IN_FLIGHT", "1")),
            video_format=source.get("SHADOW_VIDEO_FORMAT", "jpeg"),
            video_quality=int(source.get("SHADOW_VIDEO_QUALITY", "45")),
            video_scale=float(source.get("SHADOW_VIDEO_SCALE", "0.6")),
            input_stream_helper=source.get("SHADOW_INPUT_STREAM_HELPER", "/data/local/tmp/pi_input_stream"),
            token=source.get("SHADOW_TOKEN", ""),
        )
