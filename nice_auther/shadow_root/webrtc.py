"""External WebRTC gateway lifecycle and signaling client."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
from typing import Any, Callable
from urllib import error, request

from .config import ShadowConfig
from .process_log import drain_process_output, log_event


DEFAULT_WEBRTC_GATEWAY_PATH = Path(__file__).resolve().parent / "webrtc_gateway" / "build" / "nice-webrtc-gateway"


class WebRtcGateway:
    def __init__(
        self,
        config: ShadowConfig,
        *,
        popen_factory: Callable[..., subprocess.Popen[str]] | None = None,
    ) -> None:
        self.config = config
        self.popen_factory = popen_factory or subprocess.Popen
        self.process: subprocess.Popen[str] | None = None

    @property
    def host(self) -> str:
        return self.config.webrtc_gateway_host

    @property
    def port(self) -> int:
        return self.config.webrtc_gateway_port or (self.config.port + 1000)

    @property
    def rtp_port(self) -> int:
        return self.config.webrtc_rtp_port or (self.config.port + 1001)

    @property
    def control_port(self) -> int:
        return self.config.webrtc_control_port or (self.config.port + 1002)

    def command(self) -> list[str]:
        if not self.config.webrtc_gateway_managed:
            return []
        path = Path(self.config.webrtc_gateway_path) if self.config.webrtc_gateway_path else DEFAULT_WEBRTC_GATEWAY_PATH
        if not path.exists():
            raise RuntimeError(
                f"WebRTC gateway binary not found: {path}. "
                "Run python3 nice_auther/shadow_root/webrtc_gateway/build_gateway.py "
                "or set SHADOW_WEBRTC_GATEWAY_PATH."
            )
        command = [
            str(path),
            "--transport",
            self.config.webrtc_transport,
            "--listen-host",
            self.host,
            "--listen-port",
            str(self.port),
            "--ice-public-ip",
            self.config.webrtc_ice_public_ip,
            "--ice-udp-port-min",
            str(self.config.webrtc_ice_udp_port_min),
            "--ice-udp-port-max",
            str(self.config.webrtc_ice_udp_port_max),
            "--rtp-listen-host",
            self.config.webrtc_rtp_listen_host,
            "--rtp-port",
            str(self.rtp_port),
            "--agent-control-port",
            str(self.control_port),
        ]
        return command

    def start(self) -> None:
        if not self.config.webrtc_gateway_managed:
            log_event("shadow_root.webrtc_gateway", "external gateway configured", host=self.host, port=self.port)
            return
        if self.process is not None and self.process.poll() is None:
            return
        command = self.command()
        log_event("shadow_root.webrtc_gateway", "start", command=command, transport=self.config.webrtc_transport, rtp_port=self.rtp_port, control_port=self.control_port)
        self.process = self.popen_factory(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        log_event("shadow_root.webrtc_gateway", "started", pid=getattr(self.process, "pid", None))
        drain_process_output("shadow_root.webrtc_gateway", self.process)

    def stop(self) -> None:
        process = self.process
        self.process = None
        if not self.config.webrtc_gateway_managed:
            return
        if process is None:
            return
        try:
            log_event("shadow_root.webrtc_gateway", "stop", pid=getattr(process, "pid", None))
            process.terminate()
            process.wait(timeout=2)
        except Exception:
            process.kill()

    def offer(self, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"http://{self.host}:{self.port}/offer",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(body) if body else {}
            except json.JSONDecodeError:
                payload = {"error": body or str(exc)}
            if isinstance(payload, dict):
                payload.setdefault("ok", False)
                payload.setdefault("status", exc.code)
                payload.setdefault("error", str(exc))
                return payload
            return {"ok": False, "status": exc.code, "error": str(exc)}

    def status(self) -> dict[str, Any]:
        process = self.process
        effective_rtp_host = "127.0.0.1" if self.config.webrtc_transport.strip().lower() == "adb_reverse_tcp" else self.config.webrtc_rtp_host
        return {
            "enabled": True,
            "managed": self.config.webrtc_gateway_managed,
            "transport": self.config.webrtc_transport,
            "host": self.host,
            "port": self.port,
            "ice_public_ip": self.config.webrtc_ice_public_ip,
            "ice_udp_port_min": self.config.webrtc_ice_udp_port_min,
            "ice_udp_port_max": self.config.webrtc_ice_udp_port_max,
            "rtp_listen_host": self.config.webrtc_rtp_listen_host,
            "rtp_port": self.rtp_port,
            "android_rtp_host": effective_rtp_host,
            "control_port": self.control_port,
            "pid": getattr(process, "pid", None) if process is not None else None,
            "returncode": process.poll() if process is not None else None,
            "running": (process is not None and process.poll() is None) if self.config.webrtc_gateway_managed else True,
        }
