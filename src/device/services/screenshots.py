"""Atomic PNG capture support; model encoding belongs to Agent adapters."""

from pathlib import Path
from tempfile import TemporaryDirectory

from src.device.commands.executor import DEFAULT_SCREENSHOT_PATH, AndroidCommandExecutor
from src.device.types.results import CommandResult, DeviceError, make_error_result


def capture_png(executor: AndroidCommandExecutor) -> bytes | DeviceError:
    captured: CommandResult = executor.capture_screen(DEFAULT_SCREENSHOT_PATH)
    if captured.error is not None:
        return captured.error
    try:
        with TemporaryDirectory(prefix="page-image-") as directory:
            local: Path = Path(directory) / "screen.png"
            pulled: CommandResult = executor.pull_file(DEFAULT_SCREENSHOT_PATH, str(local))
            if pulled.error is not None:
                return pulled.error
            data: bytes = local.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            return make_error_result("invalid_screenshot", "Screenshot is not a PNG image")
        return data
    except OSError as exc:
        return make_error_result("screenshot_failed", str(exc))
