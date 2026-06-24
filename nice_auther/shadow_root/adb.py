"""ADB-backed helpers used by shadow_root."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path
from posixpath import dirname
from typing import Callable

from .config import ShadowConfig


DEFAULT_LOCAL_INPUT_STREAM_HELPER = Path(__file__).resolve().parent / "native" / "pi_input_stream"


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

    def start_shell(self, command: str | list[str], *, root: bool = False) -> subprocess.Popen[str]:
        args = self._adb_args(["shell", *self._shell_args(command, root=root)])
        return self.popen_factory(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

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

    def input_capabilities(self, input_device: str) -> str:
        return self.shell(["getevent", "-lp", input_device], root=True)

    def ensure_input_stream_helper(self, helper_path: str) -> str:
        if not DEFAULT_LOCAL_INPUT_STREAM_HELPER.exists():
            return helper_path
        helper_dir = dirname(helper_path.rstrip("/")) or "/data/local/tmp"
        self.shell(["mkdir", "-p", helper_dir])
        self.push_file(str(DEFAULT_LOCAL_INPUT_STREAM_HELPER), helper_path)
        self.shell(["chmod", "755", helper_path])
        return helper_path

    def push_file(self, local_path: str, remote_path: str) -> str:
        result = self.runner(self._adb_args(["push", str(local_path), str(remote_path)]))
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(output or f"adb push failed: {remote_path}")
        return remote_path

    def reverse_tcp(self, device_port: int, host_port: int) -> str:
        result = self.runner(self._adb_args(["reverse", f"tcp:{int(device_port)}", f"tcp:{int(host_port)}"]))
        if result.returncode != 0:
            output = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(output or f"adb reverse failed: tcp:{device_port} tcp:{host_port}")
        return result.stdout

    def remove_reverse_tcp(self, device_port: int) -> None:
        self.runner(self._adb_args(["reverse", "--remove", f"tcp:{int(device_port)}"]))

    def start_input_stream(self, input_device: str, helper_path: str) -> subprocess.Popen[bytes]:
        command = " ".join([shlex.quote(helper_path), shlex.quote(input_device)])
        args = self._adb_args(["shell", "su", "-c", command])
        return self.popen_factory(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def start_android_agent(self, remote_jar: str, main_class: str, args: list[str]) -> subprocess.Popen[str]:
        classpath = f"CLASSPATH={shlex.quote(remote_jar)}"
        command = " ".join([classpath, "app_process", "/", shlex.quote(main_class), *(shlex.quote(str(arg)) for arg in args)])
        return self.popen_factory(
            self._adb_args(["shell", "su", "-c", command]),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def kill_processes_matching(self, pattern: str, *, root: bool = True) -> None:
        try:
            output = self.shell(["ps", "-A", "-o", "PID,ARGS"], root=root)
        except Exception:
            return
        for line in output.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or not parts[0].isdigit():
                continue
            pid, args = parts
            if pattern not in args:
                continue
            try:
                self.shell(["kill", "-9", pid], root=root)
            except Exception:
                continue

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
