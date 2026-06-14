"""Synchronous Android device operations built on adb."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from posixpath import join as posix_join
import shlex
import subprocess
from typing import Callable, Sequence

DEFAULT_ACTION_DIR = "/sdcard/Documents/actions/"
DEFAULT_INPUT_DEVICE = "/dev/input/event3"
DEFAULT_UI_DUMP_PATH = "/sdcard/window_dump.xml"
DEFAULT_SCREENSHOT_PATH = "/sdcard/window.png"


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    def check_returncode(self) -> None:
        if self.returncode != 0:
            raise subprocess.CalledProcessError(
                self.returncode,
                self.args,
                output=self.stdout,
                stderr=self.stderr,
            )


Runner = Callable[[list[str]], CommandResult]


class AndroidDevice:
    """Small adb wrapper for command execution, files, UI dumps, and actions."""

    def __init__(self, serial: str = "", adb_path: str = "adb", runner: Runner | None = None) -> None:
        self.serial = serial
        self.adb_path = adb_path
        self.runner = runner or self._default_runner

    def shell(self, command: str | Sequence[str], root: bool = False) -> str:
        result = self.shell_raw(command, root=root)
        result.check_returncode()
        return result.stdout

    def shell_raw(self, command: str | Sequence[str], root: bool = False) -> CommandResult:
        shell_args = self._shell_args(command, root=root)
        return self._run_adb(["shell", *shell_args])

    def dump_ui(self) -> str:
        self.shell(["uiautomator", "dump", DEFAULT_UI_DUMP_PATH])
        raw_xml = self.shell(["cat", DEFAULT_UI_DUMP_PATH])
        return _strip_uiautomator_noise(raw_xml)

    def screenshot(self, device_path: str = DEFAULT_SCREENSHOT_PATH) -> str:
        self.shell(["screencap", "-p", device_path])
        return device_path

    def run_package(self, package_name: str) -> str:
        """Start an installed Android package through its launcher activity."""

        package = package_name.strip()
        if not package:
            raise ValueError("package_name must not be empty")
        return self.shell(["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])

    def execute_file(
        self,
        device_file_path: str,
        args: list[str] | None = None,
        root: bool = False,
    ) -> str:
        command = [device_file_path, *(args or [])]
        return self.shell(command, root=root)

    def list_files(self, device_dir: str) -> list[str]:
        output = self.shell(["ls", "-1", device_dir])
        return [line.strip() for line in output.splitlines() if line.strip()]

    def push_file(self, local_path: str, device_dir: str) -> str:
        remote_path = _remote_join(device_dir, Path(local_path).name)
        self.shell(["mkdir", "-p", device_dir])
        result = self._run_adb(["push", local_path, remote_path])
        result.check_returncode()
        return remote_path

    def get_supported_actions(self, device_dir: str = DEFAULT_ACTION_DIR) -> list[str]:
        self.shell(["mkdir", "-p", device_dir])
        return self.list_files(device_dir)

    def add_action(self, local_path: str, device_dir: str = DEFAULT_ACTION_DIR) -> str:
        return self.push_file(local_path, device_dir)

    def act(
        self,
        action_name: str,
        xy: tuple[int, int],
        device_dir: str = DEFAULT_ACTION_DIR,
        input_device: str = DEFAULT_INPUT_DEVICE,
    ) -> str:
        supported_actions = set(self.get_supported_actions(device_dir))
        if action_name not in supported_actions:
            available = ", ".join(sorted(supported_actions)) or "none"
            raise ValueError(f'Unknown action "{action_name}". Available actions: {available}')

        x, y = xy
        action_path = _remote_join(device_dir, action_name)
        act_script_path = '/data/local/tmp/pi_input_replay'
        return self.execute_file(
            act_script_path,
            [input_device, action_path, str(int(x)), str(int(y))],
            root=True,
        )

    def _run_adb(self, args: list[str]) -> CommandResult:
        adb_args = [self.adb_path]
        if self.serial:
            adb_args.extend(["-s", self.serial])
        adb_args.extend(args)
        return self.runner(adb_args)

    def _default_runner(self, args: list[str]) -> CommandResult:
        completed = subprocess.run(
            args,
            check=False,
            text=True,
            capture_output=True,
            encoding="utf-8",
        )
        return CommandResult(
            args=list(args),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )

    def _shell_args(self, command: str | Sequence[str], root: bool = False) -> list[str]:
        if root:
            return ["su", "-c", _command_to_shell_string(command)]
        if isinstance(command, str):
            return [command]
        return [str(part) for part in command]


def _command_to_shell_string(command: str | Sequence[str]) -> str:
    if isinstance(command, str):
        return command
    return " ".join(shlex.quote(str(part)) for part in command)


def _remote_join(device_dir: str, filename: str) -> str:
    return posix_join(device_dir.rstrip("/"), filename)


def _strip_uiautomator_noise(text: str) -> str:
    raw = str(text or "")
    for start_marker in ("<?xml", "<hierarchy"):
        start = raw.find(start_marker)
        if start >= 0:
            xml = raw[start:]
            end = xml.find("</hierarchy>")
            if end >= 0:
                return xml[: end + len("</hierarchy>")].strip()
            return xml.strip()
    return raw.strip()
