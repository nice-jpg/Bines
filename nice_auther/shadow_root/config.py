"""Configuration for shadow_root."""

from __future__ import annotations

from dataclasses import dataclass
import os


DEFAULT_INPUT_DEVICE = "/dev/input/event3"
SCHEMA_VERSION = 1
TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ShadowConfig:
    host: str = "127.0.0.1"
    bind_host: str = ""
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
    tunnel_enabled: bool = False
    tunnel_ssh_host: str = ""
    tunnel_ssh_port: int = 22
    tunnel_ssh_key: str = ""
    tunnel_remote_bind_host: str = "0.0.0.0"
    tunnel_remote_port: int = 0
    tunnel_local_host: str = "127.0.0.1"
    tunnel_extra_args: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ShadowConfig":
        source = os.environ if env is None else env
        return cls(
            host=source.get("SHADOW_HOST", "127.0.0.1"),
            bind_host=source.get("SHADOW_BIND_HOST", ""),
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
            tunnel_enabled=_env_bool(source.get("SHADOW_TUNNEL_ENABLED", "")),
            tunnel_ssh_host=source.get("SHADOW_TUNNEL_SSH_HOST", ""),
            tunnel_ssh_port=int(source.get("SHADOW_TUNNEL_SSH_PORT", "22")),
            tunnel_ssh_key=source.get("SHADOW_TUNNEL_SSH_KEY", ""),
            tunnel_remote_bind_host=source.get("SHADOW_TUNNEL_REMOTE_BIND_HOST", "0.0.0.0"),
            tunnel_remote_port=int(source.get("SHADOW_TUNNEL_REMOTE_PORT", "0")),
            tunnel_local_host=source.get("SHADOW_TUNNEL_LOCAL_HOST", "127.0.0.1"),
            tunnel_extra_args=source.get("SHADOW_TUNNEL_EXTRA_ARGS", ""),
        )


def _env_bool(value: str) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES
