"""ADB-backed helpers used by shadow_root."""

from __future__ import annotations

import re
import shlex
import subprocess
from typing import Callable

from .config import ShadowConfig


class AdbClient:
    def __init__(
        self,
        config: ShadowConfig,
        *,
        runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
        binary_runner: Callable[[list[str]], subprocess.CompletedProcess[bytes]] | None = None,
        popen_factory: Callable[..., subprocess.Popen[str]] | None = None,
    ) -> None:
        self.config = config
        self.runner = runner or self._run
        self.binary_runner = binary_runner or self._run_binary
        self.popen_factory = popen_factory or subprocess.Popen

    def shell(self, command: str | list[str], *, root: bool = False) -> str:
        args = self._adb_args(["shell", *self._shell_args(command, root=root)])
        completed = self.runner(args)
        if completed.returncode != 0:
            output = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(output or f"adb command failed: {' '.join(args)}")
        return completed.stdout

    def exec_out(self, command: str | list[str]) -> bytes:
        args = self._adb_args(["exec-out", *self._plain_args(command)])
        completed = self.binary_runner(args)
        if completed.returncode != 0:
            output = _decode_process_output(completed.stderr or completed.stdout)
            raise RuntimeError(output or f"adb command failed: {' '.join(args)}")
        return completed.stdout

    def screencap_png(self) -> bytes:
        return self.exec_out(["screencap", "-p"])

    def screen_size(self) -> tuple[int, int]:
        output = self.shell(["wm", "size"])
        match = re.search(r"(\d+)x(\d+)", output)
        if not match:
            raise RuntimeError(f"could not parse screen size from: {output!r}")
        return int(match.group(1)), int(match.group(2))

    def device_info(self) -> dict[str, str]:
        fields = {
            "model": "ro.product.model",
            "brand": "ro.product.brand",
            "sdk": "ro.build.version.sdk",
        }
        info: dict[str, str] = {}
        for key, prop in fields.items():
            try:
                info[key] = self.shell(["getprop", prop]).strip()
            except RuntimeError:
                info[key] = ""
        return info

    def inject_tap(self, x: int, y: int) -> None:
        self.shell(["input", "tap", str(int(x)), str(int(y))])

    def inject_swipe(self, start: tuple[int, int], end: tuple[int, int], duration_ms: int) -> None:
        self.shell(
            [
                "input",
                "swipe",
                str(int(start[0])),
                str(int(start[1])),
                str(int(end[0])),
                str(int(end[1])),
                str(max(1, int(duration_ms))),
            ]
        )

    def start_getevent(self, input_device: str) -> subprocess.Popen[str]:
        command = f"getevent -lt {shlex.quote(input_device)}"
        args = self._adb_args(["shell", "su", "-c", command])
        return self.popen_factory(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def _adb_args(self, args: list[str]) -> list[str]:
        adb_args = [self.config.adb_path]
        if self.config.adb_serial:
            adb_args.extend(["-s", self.config.adb_serial])
        return [*adb_args, *args]

    def _plain_args(self, command: str | list[str]) -> list[str]:
        if isinstance(command, str):
            return [command]
        return [str(part) for part in command]

    def _shell_args(self, command: str | list[str], *, root: bool = False) -> list[str]:
        if root:
            if isinstance(command, str):
                shell_command = command
            else:
                shell_command = " ".join(shlex.quote(str(part)) for part in command)
            return ["su", "-c", shell_command]
        return self._plain_args(command)

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, check=False, text=True, capture_output=True, encoding="utf-8")

    def _run_binary(self, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(args, check=False, capture_output=True)


def _decode_process_output(output: str | bytes) -> str:
    if isinstance(output, bytes):
        return output.decode("utf-8", errors="replace").strip()
    return output.strip()
