"""SSH reverse tunnel lifecycle for exposing the local shadow_root service."""

from __future__ import annotations

from dataclasses import dataclass
import shlex
import subprocess
from typing import Callable

from .config import ShadowConfig


@dataclass(frozen=True)
class TunnelEndpoint:
    remote_bind_host: str
    remote_port: int
    local_host: str
    local_port: int

    def reverse_spec(self) -> str:
        return f"{self.remote_bind_host}:{self.remote_port}:{self.local_host}:{self.local_port}"


class SshReverseTunnel:
    def __init__(
        self,
        config: ShadowConfig,
        *,
        popen_factory: Callable[..., subprocess.Popen[bytes]] | None = None,
    ) -> None:
        self.config = config
        self.popen_factory = popen_factory or subprocess.Popen
        self.process: subprocess.Popen[bytes] | None = None

    @property
    def enabled(self) -> bool:
        return self.config.tunnel_enabled

    def command(self) -> list[str]:
        if not self.config.tunnel_ssh_host:
            raise ValueError("SHADOW_TUNNEL_SSH_HOST / ShadowConfig.tunnel_ssh_host is required when tunnel is enabled")
        endpoint = TunnelEndpoint(
            remote_bind_host=self.config.tunnel_remote_bind_host,
            remote_port=self.config.tunnel_remote_port or self.config.port,
            local_host=self.config.tunnel_local_host,
            local_port=self.config.port,
        )
        command = [
            "ssh",
            "-N",
            "-T",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-p",
            str(int(self.config.tunnel_ssh_port)),
        ]
        if self.config.tunnel_ssh_key:
            command.extend(["-i", self.config.tunnel_ssh_key])
        if self.config.tunnel_extra_args:
            command.extend(shlex.split(self.config.tunnel_extra_args))
        command.extend(["-R", endpoint.reverse_spec(), self.config.tunnel_ssh_host])
        return command

    def start(self) -> None:
        if not self.enabled:
            return
        if self.process and self.process.poll() is None:
            return
        self.process = self.popen_factory(
            self.command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def stop(self) -> None:
        process = self.process
        self.process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except Exception:
            process.kill()
            process.wait(timeout=5)


def tunnel_access_url(config: ShadowConfig) -> str:
    remote_host = _display_host(config.host or config.tunnel_ssh_host)
    remote_port = config.tunnel_remote_port or config.port
    return f"http://{remote_host}:{remote_port}"


def _display_host(host: str) -> str:
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if host in {"", "0.0.0.0", "::"}:
        host = "127.0.0.1"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host
