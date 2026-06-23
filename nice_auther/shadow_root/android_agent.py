"""Android-side H.264 shadow agent lifecycle."""

from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any

from .adb import AdbClient
from .config import ShadowConfig
from .process_log import drain_process_output, log_event


class AndroidShadowAgent:
    def __init__(self, adb: AdbClient, config: ShadowConfig) -> None:
        self.adb = adb
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self.remote_path = config.android_agent_remote_path

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        self.validate_config()
        log_event("shadow_root.android_agent", "push start", local_path=self.config.android_agent_jar, remote_path=self.remote_path)
        self.adb.push_file(str(Path(self.config.android_agent_jar)), self.remote_path)
        self.adb.shell(["chmod", "644", self.remote_path], root=True)
        log_event("shadow_root.android_agent", "kill stale", pattern=self.config.android_agent_main_class)
        self.adb.kill_processes_matching(self.config.android_agent_main_class, root=True)
        args = self.agent_args()
        log_event("shadow_root.android_agent", "start", remote_path=self.remote_path, main_class=self.config.android_agent_main_class, args=args)
        self.process = self.adb.start_android_agent(self.remote_path, self.config.android_agent_main_class, args)
        log_event("shadow_root.android_agent", "started", pid=getattr(self.process, "pid", None), params=self.params())
        drain_process_output("shadow_root.android_agent", self.process)

    def validate_config(self) -> None:
        if not self.config.android_agent_jar:
            raise RuntimeError("SHADOW_ANDROID_AGENT_JAR is required for webrtc_h264")
        local_path = Path(self.config.android_agent_jar)
        if not local_path.exists():
            raise RuntimeError(f"Android agent jar not found: {local_path}")

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            log_event("shadow_root.android_agent", "stop", pid=getattr(process, "pid", None))
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            process.kill()

    def status(self) -> dict[str, Any]:
        process = self.process
        return {
            "enabled": True,
            "remote_path": self.remote_path,
            "main_class": self.config.android_agent_main_class,
            "pid": getattr(process, "pid", None) if process is not None else None,
            "returncode": process.poll() if process is not None else None,
            "running": process is not None and process.poll() is None,
            "params": self.params(),
        }

    def params(self) -> dict[str, Any]:
        transport = self.config.webrtc_transport.strip().lower()
        return {
            "transport": transport,
            "max_size": self.config.video_max_size,
            "fps": self.config.video_fps,
            "bitrate": self.config.video_bitrate,
            "i_frame_interval_ms": self.config.video_iframe_interval_ms,
            "rtp_host": "127.0.0.1" if transport == "adb_reverse_tcp" else self.config.webrtc_rtp_host,
            "rtp_port": self.config.webrtc_rtp_port or (self.config.port + 1001),
            "control_port": self.config.webrtc_control_port or (self.config.port + 1002),
            "mtu": self.config.webrtc_rtp_mtu,
        }

    def agent_args(self) -> list[str]:
        params = self.params()
        args = [
            "--transport",
            str(params["transport"]),
            "--max-size",
            str(params["max_size"]),
            "--fps",
            str(params["fps"]),
            "--bitrate",
            str(params["bitrate"]),
            "--i-frame-interval-ms",
            str(params["i_frame_interval_ms"]),
            "--rtp-host",
            str(params["rtp_host"]),
            "--rtp-port",
            str(params["rtp_port"]),
            "--control-port",
            str(params["control_port"]),
            "--mtu",
            str(params["mtu"]),
        ]
        if self.config.android_agent_self_test_rtp:
            args.append("--self-test-rtp")
        return args
