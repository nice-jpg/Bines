"""Configuration for shadow_root."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import socket
import subprocess


DEFAULT_INPUT_DEVICE = "/dev/input/event3"
ENV_DIR = Path(__file__).resolve().parent / "env"
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
    video_backend: str = "webrtc_h264"
    video_fps: int = 30
    video_max_in_flight: int = 1
    video_format: str = "jpeg"
    video_quality: int = 45
    video_scale: float = 0.6
    video_max_size: int = 720
    video_bitrate: str = "2M"
    video_iframe_interval_ms: int = 1000
    webrtc_gateway_path: str = ""
    webrtc_gateway_managed: bool = True
    webrtc_gateway_host: str = "127.0.0.1"
    webrtc_gateway_port: int = 0
    webrtc_ice_public_ip: str = ""
    webrtc_ice_udp_port_min: int = 0
    webrtc_ice_udp_port_max: int = 0
    webrtc_transport: str = "adb_reverse_tcp"
    webrtc_rtp_host: str = ""
    webrtc_rtp_listen_host: str = "0.0.0.0"
    webrtc_rtp_port: int = 0
    webrtc_control_port: int = 0
    webrtc_rtp_mtu: int = 1200
    android_agent_self_test_rtp: bool = False
    android_agent_jar: str = ""
    android_agent_remote_path: str = "/data/local/tmp/nice_shadow_agent.jar"
    android_agent_main_class: str = "nice.auther.shadow.AgentMain"
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

    def __post_init__(self) -> None:
        if not self.webrtc_rtp_host:
            object.__setattr__(self, "webrtc_rtp_host", _default_rtp_host())

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ShadowConfig":
        source = _config_source(env)
        return cls(
            host=source.get("SHADOW_HOST", "127.0.0.1"),
            bind_host=source.get("SHADOW_BIND_HOST", ""),
            port=int(source.get("SHADOW_PORT", "8765")),
            adb_path=source.get("SHADOW_ADB_PATH", "adb"),
            adb_serial=source.get("SHADOW_ADB_SERIAL", ""),
            input_device=source.get("SHADOW_INPUT_DEVICE", DEFAULT_INPUT_DEVICE),
            frame_interval_ms=int(source.get("SHADOW_FRAME_INTERVAL_MS", "500")),
            video_backend=source.get("SHADOW_VIDEO_BACKEND", "webrtc_h264"),
            video_fps=int(source.get("SHADOW_VIDEO_FPS", "30")),
            video_max_in_flight=int(source.get("SHADOW_VIDEO_MAX_IN_FLIGHT", "1")),
            video_format=source.get("SHADOW_VIDEO_FORMAT", "jpeg"),
            video_quality=int(source.get("SHADOW_VIDEO_QUALITY", "45")),
            video_scale=float(source.get("SHADOW_VIDEO_SCALE", "0.6")),
            video_max_size=int(source.get("SHADOW_VIDEO_MAX_SIZE", "720")),
            video_bitrate=source.get("SHADOW_VIDEO_BITRATE", "2M"),
            video_iframe_interval_ms=int(source.get("SHADOW_VIDEO_IFRAME_INTERVAL_MS", "1000")),
            webrtc_gateway_path=source.get("SHADOW_WEBRTC_GATEWAY_PATH", ""),
            webrtc_gateway_managed=_env_bool(source.get("SHADOW_WEBRTC_GATEWAY_MANAGED", "true")),
            webrtc_gateway_host=source.get("SHADOW_WEBRTC_GATEWAY_HOST", "127.0.0.1"),
            webrtc_gateway_port=int(source.get("SHADOW_WEBRTC_GATEWAY_PORT", "0")),
            webrtc_ice_public_ip=source.get("SHADOW_WEBRTC_ICE_PUBLIC_IP", ""),
            webrtc_ice_udp_port_min=int(source.get("SHADOW_WEBRTC_ICE_UDP_PORT_MIN", "0")),
            webrtc_ice_udp_port_max=int(source.get("SHADOW_WEBRTC_ICE_UDP_PORT_MAX", "0")),
            webrtc_transport=source.get("SHADOW_WEBRTC_TRANSPORT", "adb_reverse_tcp"),
            webrtc_rtp_host=source.get("SHADOW_WEBRTC_RTP_HOST", ""),
            webrtc_rtp_listen_host=source.get("SHADOW_WEBRTC_RTP_LISTEN_HOST", "0.0.0.0"),
            webrtc_rtp_port=int(source.get("SHADOW_WEBRTC_RTP_PORT", "0")),
            webrtc_control_port=int(source.get("SHADOW_WEBRTC_CONTROL_PORT", "0")),
            webrtc_rtp_mtu=int(source.get("SHADOW_WEBRTC_RTP_MTU", "1200")),
            android_agent_self_test_rtp=_env_bool(source.get("SHADOW_ANDROID_AGENT_SELF_TEST_RTP", "")),
            android_agent_jar=source.get("SHADOW_ANDROID_AGENT_JAR", ""),
            android_agent_remote_path=source.get("SHADOW_ANDROID_AGENT_REMOTE_PATH", "/data/local/tmp/nice_shadow_agent.jar"),
            android_agent_main_class=source.get("SHADOW_ANDROID_AGENT_MAIN_CLASS", "nice.auther.shadow.AgentMain"),
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


def _config_source(env: dict[str, str] | None) -> dict[str, str]:
    if env is not None:
        return env
    source = _load_env_directory(ENV_DIR)
    source.update(os.environ)
    return source


def _load_env_directory(path: Path = ENV_DIR) -> dict[str, str]:
    if path.is_file():
        return _read_env_file(path)
    if not path.is_dir():
        return {}
    values: dict[str, str] = {}
    for env_file in sorted(path.glob("*.env")):
        values.update(_read_env_file(env_file))
    return values


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_env_value(value.strip())
    return values


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _env_bool(value: str) -> bool:
    return str(value or "").strip().lower() in TRUE_VALUES


def _default_rtp_host() -> str:
    host = _default_rtp_host_from_route()
    if host != "127.0.0.1":
        return host
    host = _default_rtp_host_from_ifconfig()
    if host:
        return host
    return "127.0.0.1"


def _default_rtp_host_from_route() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def _default_rtp_host_from_ifconfig() -> str:
    try:
        output = subprocess.run(["ifconfig"], check=False, capture_output=True, text=True).stdout
    except Exception:
        return ""
    block: list[str] = []
    for raw_line in output.splitlines():
        if raw_line and not raw_line.startswith(("\t", " ")):
            host = _host_from_ifconfig_block(block)
            if host:
                return host
            block = [raw_line]
        else:
            block.append(raw_line)
    return _host_from_ifconfig_block(block)


def _host_from_ifconfig_block(block: list[str]) -> str:
    if not block or not any("status: active" in line for line in block):
        return ""
    for raw_line in block:
        line = raw_line.strip()
        if line.startswith("inet "):
            host = line.split()[1]
            if not host.startswith("127.") and not host.startswith("169.254."):
                return host
    return ""
