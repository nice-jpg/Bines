"""Network reachability diagnostics for the WebRTC H.264 path."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import shlex
import socket
import time
from typing import Any

from .adb import AdbClient
from .config import ShadowConfig


@dataclass(frozen=True)
class ReachabilityResult:
    ok: bool
    probe: str
    target_host: str
    target_port: int
    listen_host: str
    elapsed_ms: int
    received_from: str = ""
    bytes_received: int = 0
    error: str = ""
    command: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def check_android_udp_reachability(adb: AdbClient, config: ShadowConfig, *, timeout_s: float = 1.5) -> ReachabilityResult:
    """Ask Android to send one UDP packet to the host RTP port and wait locally."""

    target_host = config.webrtc_rtp_host
    target_port = config.webrtc_rtp_port or (config.port + 1001)
    listen_host = config.webrtc_rtp_listen_host
    bind_host = "" if listen_host in {"", "0.0.0.0"} else listen_host
    probe = f"nice-shadow-probe-{time.monotonic_ns()}"
    started = time.monotonic()
    command = _udp_probe_command(probe, target_host, target_port)
    process = None

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_s)
            sock.bind((bind_host, target_port))
            try:
                process = adb.start_shell(command)
            except Exception as exc:
                return _result(
                    False,
                    probe,
                    target_host,
                    target_port,
                    listen_host,
                    started,
                    error=f"android probe command failed: {exc}",
                    command=command,
                )
            while True:
                data, address = sock.recvfrom(2048)
                if data.decode("utf-8", errors="replace") == probe:
                    return _result(
                        True,
                        probe,
                        target_host,
                        target_port,
                        listen_host,
                        started,
                        received_from=f"{address[0]}:{address[1]}",
                        bytes_received=len(data),
                        command=command,
                    )
    except socket.timeout:
        return _result(False, probe, target_host, target_port, listen_host, started, error="timeout waiting for Android UDP probe", command=command)
    except OSError as exc:
        return _result(False, probe, target_host, target_port, listen_host, started, error=f"local UDP bind/listen failed: {exc}", command=command)
    finally:
        _stop_probe_process(process)


def log_reachability_result(result: ReachabilityResult) -> None:
    payload = {
        "component": "shadow_root.reachability",
        "message": "android udp reachability",
        **result.to_dict(),
    }
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def _udp_probe_command(probe: str, host: str, port: int) -> str:
    quoted_probe = shlex.quote(probe)
    quoted_host = shlex.quote(host)
    quoted_port = shlex.quote(str(port))
    return (
        "sh -c "
        + shlex.quote(
            "printf %s "
            + quoted_probe
            + " | (toybox nc -u -w 1 "
            + quoted_host
            + " "
            + quoted_port
            + " || nc -u -w 1 "
            + quoted_host
            + " "
            + quoted_port
            + ")"
        )
    )


def _result(
    ok: bool,
    probe: str,
    target_host: str,
    target_port: int,
    listen_host: str,
    started: float,
    *,
    received_from: str = "",
    bytes_received: int = 0,
    error: str = "",
    command: str = "",
) -> ReachabilityResult:
    return ReachabilityResult(
        ok=ok,
        probe=probe,
        target_host=target_host,
        target_port=target_port,
        listen_host=listen_host,
        elapsed_ms=round((time.monotonic() - started) * 1000),
        received_from=received_from,
        bytes_received=bytes_received,
        error=error,
        command=command,
    )


def _stop_probe_process(process: Any) -> None:
    if process is None:
        return
    try:
        if process.poll() is not None:
            return
        process.terminate()
        process.wait(timeout=0.5)
    except Exception:
        try:
            process.kill()
        except Exception:
            return
