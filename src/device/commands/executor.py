"""The finite Android command boundary; no gesture or model policy lives here."""

import re
import shlex
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from posixpath import join
from typing import Final

from src.device.types.results import CommandResult, DeviceError, make_error_result

DEFAULT_ACTION_DIR: Final[str] = "/sdcard/Documents/actions/"
DEFAULT_INPUT_DEVICE: Final[str] = "/dev/input/event3"
DEFAULT_UI_DUMP_PATH: Final[str] = "/sdcard/window_dump.xml"
DEFAULT_SCREENSHOT_PATH: Final[str] = "/sdcard/window.png"
ACT_SCRIPT_PATH: Final[str] = "/data/local/tmp/pi_input_replay"

type Runner = Callable[[list[str]], CommandResult]


class AndroidCommandExecutor:
    def __init__(self) -> None:
        self.serial: str = ""
        self.adb_path: str = "adb"
        self.runner: Runner = self._default_runner

    def start_package(self, package: str) -> CommandResult:
        return self._shell(["monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])

    def stop_package(self, package: str) -> CommandResult:
        return self._shell(["am", "force-stop", package])

    def disable_animations(self) -> CommandResult:
        result: CommandResult = CommandResult([], 0, "", "")
        for setting in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
            result = self._shell(["settings", "put", "global", setting, "0"])
            if not result.ok:
                return result
        return result

    def dump_hierarchy(self) -> CommandResult:
        return self._shell(["/data/local/tmp/project", "-d", DEFAULT_UI_DUMP_PATH])

    def read_file(self, path: str) -> CommandResult:
        return self._shell(["cat", path])

    def capture_screen(self, path: str) -> CommandResult:
        return self._shell(["screencap", "-p", path])

    def list_files(self, directory: str) -> CommandResult:
        # An absent action directory is an empty deployment, not an ls failure.
        created: CommandResult = self._shell(["mkdir", "-p", directory])
        return created if not created.ok else self._shell(["ls", "-1", directory])

    def push_file(self, local: str, directory: str) -> CommandResult:
        created: CommandResult = self._shell(["mkdir", "-p", directory])
        if not created.ok:
            return created
        return self._run_adb(["push", local, join(directory, Path(local).name)])

    def pull_file(self, remote: str, local: str) -> CommandResult:
        return self._run_adb(["pull", remote, local])

    def replay(self, path: str, x: int, y: int) -> CommandResult:
        command: list[str] = [ACT_SCRIPT_PATH, DEFAULT_INPUT_DEVICE, path, str(x), str(y)]
        return self._run_adb(["shell", "su", "-c", shlex.join(command)])

    def _shell(self, command: Sequence[str]) -> CommandResult:
        return self._run_adb(["shell", *command])

    def _run_adb(self, args: list[str]) -> CommandResult:
        command: list[str] = [self.adb_path]
        if self.serial:
            command.extend(["-s", self.serial])
        command.extend(args)
        try:
            result: CommandResult = self.runner(command)
        except Exception as exc:  # noqa: BLE001 - device command exceptions are data.
            return CommandResult(
                command,
                None,
                "",
                str(exc),
                make_error_result("command_error", f"{type(exc).__name__}: {exc}", details={"args": command}),
            )
        error: DeviceError | None = result.error
        if error is None and (result.returncode != 0 or _has_adb_error_signal(result.stdout, result.stderr)):
            error = make_error_result(
                "command_error",
                (result.stderr or result.stdout).strip() or "Command failed",
                details={
                    "args": command,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
            )
        return CommandResult(command, result.returncode, result.stdout, result.stderr, error)

    def _default_runner(self, args: list[str]) -> CommandResult:
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            args, check=False, text=True, capture_output=True, encoding="utf-8"
        )
        return CommandResult(args, completed.returncode, completed.stdout, completed.stderr)


def _has_adb_error_signal(stdout: str, stderr: str) -> bool:
    patterns: tuple[str, ...] = (
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
    return any(
        re.search(pattern, line.strip())
        for line in f"{stdout}\n{stderr}".splitlines()
        for pattern in patterns
    )
