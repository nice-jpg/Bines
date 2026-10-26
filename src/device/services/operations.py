"""Serialized device operations and synchronized page evidence."""

import time
from collections.abc import Callable
from threading import RLock
from typing import Final

from src.common.event_log import WorkspaceEventLogger
from src.device.commands.executor import DEFAULT_SCREENSHOT_PATH, AndroidCommandExecutor
from src.device.commands.shadow_control import start_shadow_service, stop_shadow_service
from src.device.services.execution import DeviceOperationExecutor
from src.device.services.gestures import GestureReplay
from src.device.services.page.bridge import PageBridge, PageOperationResult
from src.device.services.screenshots import capture_png
from src.device.types.observations import CollectionSwipe, SwipeObservation
from src.device.types.results import CommandResult, DeviceError, make_error_result
from src.device.types.shadow import ShadowServiceStatus

DEFAULT_POST_ACTION_UI_DELAY_SECONDS: Final[float] = 1.2
_POST_ACTION_SLEEP: Final[Callable[[float], None]] = time.sleep


class DeviceOperationService:
    """Central owner for recorded touch-operation replay."""

    def __init__(self) -> None:
        self.executor: AndroidCommandExecutor = AndroidCommandExecutor()
        self.gestures: GestureReplay = GestureReplay(self.executor)
        self.logger: WorkspaceEventLogger = WorkspaceEventLogger()
        self.post_action_ui_delay_seconds: float = DEFAULT_POST_ACTION_UI_DELAY_SECONDS
        self._sleep: Callable[[float], None] = _POST_ACTION_SLEEP
        self.operation_lock: RLock = RLock()
        self.operation_executor: DeviceOperationExecutor = DeviceOperationExecutor()
        self.page_bridge: PageBridge = PageBridge(self.executor, lock=self.operation_lock)

    def tap(self, x: int, y: int) -> PageOperationResult:
        """Replay the recorded tap action."""

        return self._replay_recorded_action("tap", x, y)

    def swipe_up(self, x: int, y: int) -> PageOperationResult:
        """Replay the recorded upward swipe action."""

        return self._replay_recorded_action("swipe_up", x, y)

    def swipe_up_long(self, x: int, y: int) -> PageOperationResult:
        """Replay the recorded long upward swipe action."""

        return self._replay_recorded_action("swipe_up_long", x, y)

    def swipe_to_random(self, x: int, y: int) -> PageOperationResult:
        """Replay the recorded swipe used to select a random training state."""

        return self._replay_recorded_action("swipe_to_random", x, y)

    def swipe_down(self, x: int, y: int) -> PageOperationResult:
        """Replay the recorded downward swipe action."""

        return self._replay_recorded_action("swipe_down", x, y)

    def swipe_back(self) -> PageOperationResult:
        """Replay the recorded back action."""

        return self._replay_recorded_action("swipe_back", 0, 2000)

    def screenshot(self) -> str | DeviceError:
        with self.operation_lock:
            result: CommandResult = self.executor.capture_screen(DEFAULT_SCREENSHOT_PATH)
            return result.error if result.error is not None else DEFAULT_SCREENSHOT_PATH

    def capture_png(self) -> bytes | DeviceError:
        with self.operation_lock:
            return capture_png(self.executor)

    def run_package(self, package_name: str) -> str | DeviceError:
        with self.operation_lock:
            result: str | DeviceError = self._package_operation(package_name, self.executor.start_package)
            if isinstance(result, str):
                self.page_bridge.reset()
            return result

    def close_package(self, package_name: str) -> str | DeviceError:
        with self.operation_lock:
            return self._package_operation(package_name, self.executor.stop_package)

    def _package_operation(
        self, package_name: str, command: Callable[[str], CommandResult]
    ) -> str | DeviceError:
        package: str = package_name.strip()
        if not package:
            return make_error_result("validation_error", "package_name must not be empty")
        result: CommandResult = command(package)
        if result.error is not None:
            self._log_error_result(result.error)
            return result.error
        return result.stdout

    def start_shadow_service(self) -> ShadowServiceStatus:
        return start_shadow_service()

    def stop_shadow_service(self) -> ShadowServiceStatus:
        return stop_shadow_service()

    def _replay_recorded_action(self, action_name: str, x: int, y: int) -> PageOperationResult:
        """Replay the action, then synchronize the shared abstract page view."""
        with self.operation_lock:
            return self._replay_and_refresh(action_name, x, y)

    def collection_swipe(self, action: CollectionSwipe, x: int, y: int) -> SwipeObservation:
        """Execute a collection gesture and report its whole-page view update ratio."""
        with self.operation_lock:
            page, artifact = self._replay_and_refresh(action.value, x, y)
            if artifact is None:
                return SwipeObservation({}, None, None, "device_operation_failed")
            return SwipeObservation(page, artifact["page_snapshot"], self.page_bridge.root_change_ratio())

    def _replay_and_refresh(self, action_name: str, x: int, y: int) -> PageOperationResult:
        action_result: str | DeviceError = self._run_device_operation(
            action_name, lambda: self.gestures.replay(action_name, x, y)
        )
        if isinstance(action_result, DeviceError):
            return action_result.as_dict(), None
        self._wait_after_action()
        return self.page_bridge.refresh()

    def _wait_after_action(self) -> None:
        if self.post_action_ui_delay_seconds > 0:
            self._sleep(self.post_action_ui_delay_seconds)

    def _run_device_operation(
        self, name: str, operation: Callable[[], str | DeviceError]
    ) -> str | DeviceError:
        with self.operation_lock:
            return self._execute_device_operation(name, operation)

    def _execute_device_operation(
        self, name: str, operation: Callable[[], str | DeviceError]
    ) -> str | DeviceError:
        try:
            result: str | DeviceError = operation()
        except Exception as exc:  # noqa: BLE001 - tools return structured errors instead of raising.
            return self._exception_result(name, exc)
        if isinstance(result, DeviceError):
            self._log_error_result(result)
            return result
        return result

    def _exception_result(self, operation: str, exc: Exception) -> DeviceError:
        result: DeviceError = make_error_result(
            "other_exception",
            f"{type(exc).__name__}: {exc}",
            details={"operation": operation},
        )
        self._log_error_result(result)
        return result

    def _log_error_result(self, result: DeviceError) -> None:
        category: str = result.error_type
        self.logger.log(category, result.message, result.details)
