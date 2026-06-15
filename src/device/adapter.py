"""Synchronous Android device operations built on adb."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from posixpath import join as posix_join
import re
import shlex
import subprocess
from typing import Callable, Sequence

try:
    from .results import ErrorResult, is_error_result, make_error_result
except ImportError:  # Supports direct PYTHONPATH=src imports.
    from device.results import ErrorResult, is_error_result, make_error_result

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

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def check_returncode(self) -> None | ErrorResult:
        if self.returncode != 0:
            return _command_error_result(self)
        return None


Runner = Callable[[list[str]], CommandResult]


class AndroidDevice:
    """Small adb wrapper for command execution, files, UI dumps, and actions."""

    def __init__(self, serial: str = "", adb_path: str = "adb", runner: Runner | None = None) -> None:
        self.serial = serial
        self.adb_path = adb_path
        self.runner = runner or self._default_runner

    def shell(self, command: str | Sequence[str], root: bool = False) -> str | ErrorResult:
        result = self.shell_raw(command, root=root)
        return _command_stdout_or_error(result)

    def shell_raw(self, command: str | Sequence[str], root: bool = False) -> CommandResult:
        shell_args = self._shell_args(command, root=root)
        return self._run_adb(["shell", *shell_args])

    def dump_ui(self) -> str | ErrorResult:
        animation_result = self._disable_animations()
        if is_error_result(animation_result):
            return animation_result
        dump_result = self.shell(["uiautomator", "dump", DEFAULT_UI_DUMP_PATH])
        if is_error_result(dump_result):
            return dump_result
        raw_xml = self.shell(["cat", DEFAULT_UI_DUMP_PATH])
        if is_error_result(raw_xml):
            return raw_xml
        return _strip_uiautomator_noise(raw_xml)

    def _disable_animations(self) -> None | ErrorResult:
        for setting_name in (
            "window_animation_scale",
            "transition_animation_scale",
            "animator_duration_scale",
        ):
            result = self.shell(["settings", "put", "global", setting_name, "0"])
            if is_error_result(result):
                return result
        return None

    def screenshot(self, device_path: str = DEFAULT_SCREENSHOT_PATH) -> str | ErrorResult:
        result = self.shell(["screencap", "-p", device_path])
        if is_error_result(result):
            return result
        return device_path

    def run_package(self, package_name: str) -> str | ErrorResult:
        """Start an installed Android package through its launcher activity."""

        package = package_name.strip()
        if not package:
            return make_error_result("validation_error", "package_name must not be empty")
        return self.shell(["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])

    def execute_file(
        self,
        device_file_path: str,
        args: list[str] | None = None,
        root: bool = False,
    ) -> str | ErrorResult:
        command = [device_file_path, *(args or [])]
        return self.shell(command, root=root)

    def list_files(self, device_dir: str) -> list[str] | ErrorResult:
        output = self.shell(["ls", "-1", device_dir])
        if is_error_result(output):
            return output
        return [line.strip() for line in output.splitlines() if line.strip()]

    def push_file(self, local_path: str, device_dir: str) -> str | ErrorResult:
        remote_path = _remote_join(device_dir, Path(local_path).name)
        mkdir_result = self.shell(["mkdir", "-p", device_dir])
        if is_error_result(mkdir_result):
            return mkdir_result
        result = self._run_adb(["push", local_path, remote_path])
        if not result.ok:
            return _command_error_result(result)
        return remote_path

    def get_supported_actions(self, device_dir: str = DEFAULT_ACTION_DIR) -> list[str] | ErrorResult:
        mkdir_result = self.shell(["mkdir", "-p", device_dir])
        if is_error_result(mkdir_result):
            return mkdir_result
        return self.list_files(device_dir)

    def add_action(self, local_path: str, device_dir: str = DEFAULT_ACTION_DIR) -> str | ErrorResult:
        return self.push_file(local_path, device_dir)

    def act(
        self,
        action_name: str,
        xy: tuple[int, int],
        device_dir: str = DEFAULT_ACTION_DIR,
        input_device: str = DEFAULT_INPUT_DEVICE,
    ) -> str | ErrorResult:
        supported_action_result = self.get_supported_actions(device_dir)
        if is_error_result(supported_action_result):
            return supported_action_result
        supported_actions = set(supported_action_result)
        if action_name not in supported_actions:
            available = ", ".join(sorted(supported_actions)) or "none"
            return make_error_result(
                "validation_error",
                f'Unknown action "{action_name}". Available actions: {available}',
                details={"action_name": action_name, "available_actions": sorted(supported_actions)},
            )

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
        try:
            return self.runner(adb_args)
        except Exception as exc:  # noqa: BLE001 - convert runner failures into command results.
            return CommandResult(
                args=adb_args,
                returncode=1,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
            )

    def _default_runner(self, args: list[str]) -> CommandResult:
        try:
            completed = subprocess.run(
                args,
                check=False,
                text=True,
                capture_output=True,
                encoding="utf-8",
            )
        except Exception as exc:  # noqa: BLE001 - convert subprocess failures into command results.
            return CommandResult(
                args=list(args),
                returncode=1,
                stdout="",
                stderr=f"{type(exc).__name__}: {exc}",
            )
        return CommandResult(
            args=list(args),
            returncode=_normalize_adb_returncode(completed.returncode, completed.stdout, completed.stderr),
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


def _command_stdout_or_error(result: CommandResult) -> str | ErrorResult:
    if result.ok:
        return result.stdout
    return _command_error_result(result)


def _command_error_result(result: CommandResult) -> ErrorResult:
    return make_error_result(
        "command_error",
        _command_error_message(result),
        details={
            "args": result.args,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        },
    )


def _command_error_message(result: CommandResult) -> str:
    output = (result.stderr or result.stdout or "").strip()
    if output:
        return output
    return f"Command failed with return code {result.returncode}: {_command_to_shell_string(result.args)}"


def _normalize_adb_returncode(returncode: int, stdout: str, stderr: str) -> int:
    if returncode != 0:
        return returncode
    if _has_adb_error_signal(stdout, stderr):
        return 1
    return returncode


def _has_adb_error_signal(stdout: str, stderr: str) -> bool:
    output = f"{stdout}\n{stderr}"
    return any(_is_adb_error_line(line) for line in output.splitlines())


def _is_adb_error_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    error_patterns = (
        r"^(?:ERROR|Error|error)(?::|\b)",
        r"^(?:java\.)?\w*(?:Exception|Error)(?::|\b)",
        r"^Failure\s+\[",
        r"^Security exception\b",
        r"^Permission denied\b",
        r"^No such file or directory\b",
        r"^not found\b",
        r"^Unknown option\b",
        r"^Killed\b",
    )
    return any(re.search(pattern, stripped) for pattern in error_patterns)
