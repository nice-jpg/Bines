"""Recorded touch operations exposed as individual LangChain tools.

Each operation is fire-and-forget: the agent calls a specific tool with a
random ``(x, y)`` coordinate, then the hub replays the matching recorded action.
The replay backend is intentionally left as a placeholder.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import time

from langchain_core.tools import StructuredTool

try:
    from src.device.adapter import AndroidDevice
    from src.device.results import ErrorResult, is_error_result, make_error_result
    from src.device.translator import check_actions
    from src.tools.event_logger import WorkspaceEventLogger
    from src.tools.optimize_xml import optimize
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from device.adapter import AndroidDevice
    from device.results import ErrorResult, is_error_result, make_error_result
    from device.translator import check_actions
    from tools.event_logger import WorkspaceEventLogger
    from tools.optimize_xml import optimize

DEFAULT_POST_ACTION_UI_DELAY_SECONDS = 1.2


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    operation_name: str
    parameters: tuple[str, ...]


TOOL_SPECS = (
    ToolSpec("run_package", "Open an Android application by package name.", "run_package", ("package_name",)),
    ToolSpec("close_package", "Force-stop an Android application by package name.", "close_package", ("package_name",)),
    ToolSpec("tap", "tap (x, y). Provide x/y coordinates. Returns no operation data.", "tap", ("x", "y")),
    ToolSpec(
        "swipe_up",
        "swipe up from (x, y) for a short distance, screen will roll up. Use this tool to load more data below. Provide x/y coordinates. Returns current UIAutomator XML hierarchy.",
        "swipe_up",
        ("x", "y"),
    ),
    ToolSpec(
        "swipe_down",
        "swipe down from (x, y) for a short distance, screen will go down Use this tool to load more data from above, or to refresh current page. Provide x/y coordinates. Returns current UIAutomator XML hierarchy.",
        "swipe_down",
        ("x", "y"),
    ),
    ToolSpec(
        "swipe_back",
        "return to the last page. Returns current UIAutomator XML hierarchy.",
        "swipe_back",
        (),
    ),
    ToolSpec("uiautomate", "Get the current UIAutomator XML hierarchy.", "uiautomate", ()),
    ToolSpec("screenshot", "Capture the current screen and return the remote image path.", "screenshot", ()),
)

EVENT_HUB_TOOL_NAMES = tuple(spec.name for spec in TOOL_SPECS)


class EventHub:
    """Central owner for recorded touch-operation replay."""

    def __init__(
        self,
        logger: WorkspaceEventLogger | None = None,
        sleep_func: Callable[[float], None] = time.sleep,
    ) -> None:
        self.device = AndroidDevice()
        self._actions_checked = False
        self.logger = logger or WorkspaceEventLogger()
        self.post_action_ui_delay_seconds = DEFAULT_POST_ACTION_UI_DELAY_SECONDS
        self._sleep = sleep_func

    def tap(self, x: int, y: int) -> str | ErrorResult:
        """Replay the recorded tap action."""

        return self._replay_recorded_action("tap", x, y)

    def swipe_up(self, x: int, y: int) -> str | ErrorResult:
        """Replay the recorded upward swipe action."""

        return self._replay_recorded_action("swipe_up", x, y)

    def swipe_down(self, x: int, y: int) -> str | ErrorResult:
        """Replay the recorded downward swipe action."""

        return self._replay_recorded_action("swipe_down", x, y)

    def swipe_back(self) -> str | ErrorResult:
        """Replay the recorded back action."""

        return self._replay_recorded_action("swipe_back", 0, 2000)

    def uiautomate(self) -> str | ErrorResult:
        """Return the current UIAutomator XML hierarchy."""
        result = self._run_device_operation("uiautomate", self.device.dump_ui, inspect_page=True)
        if type(result) is ErrorResult:
            return result
        assert(isinstance(result, str))
        return optimize(result)

    def screenshot(self) -> str | ErrorResult:
        """Capture a screenshot on the device and return the remote path."""

        return self._run_device_operation("screenshot", self.device.screenshot)

    def run_package(self, package_name: str) -> str | ErrorResult:
        """Open the specified Android application package."""

        return self._run_device_operation("run_package", lambda: self.device.run_package(package_name))

    def close_package(self, package_name: str) -> str | ErrorResult:
        """Force-stop the specified Android application package."""

        return self._run_device_operation("close_package", lambda: self.device.close_package(package_name))

    def _replay_recorded_action(self, action_name: str, x: int, y: int) -> str | ErrorResult:
        """Replay a recorded action with coordinate jitter input.

        The concrete replay implementation is intentionally empty for now.
        Future code should map ``action_name`` to a recorded action asset and
        use ``x`` / ``y`` only as randomness input.
        """
        action_check_result = self._ensure_actions_checked()
        if is_error_result(action_check_result):
            return action_check_result
        action_result = self._run_device_operation(
            action_name,
            lambda: self.device.act(action_name=action_name, xy=(x, y)),
        )
        if is_error_result(action_result):
            return action_result
        self._wait_after_action()
        return self.uiautomate()

    def _wait_after_action(self) -> None:
        if self.post_action_ui_delay_seconds > 0:
            self._sleep(self.post_action_ui_delay_seconds)

    def _ensure_actions_checked(self) -> None | ErrorResult:
        if self._actions_checked:
            return None
        try:
            check_actions(self.device)
        except Exception as exc:  # noqa: BLE001 - return structured tool errors.
            return self._exception_result("action_check", exc)
        self._actions_checked = True
        return None

    def _run_device_operation(self, name: str, operation: Callable, inspect_page: bool = False) -> str | ErrorResult:
        try:
            result = operation()
        except Exception as exc:  # noqa: BLE001 - tools return structured errors instead of raising.
            return self._exception_result(name, exc)
        if is_error_result(result):
            self._log_error_result(result)
            return result
        if inspect_page and isinstance(result, str):
            self._log_page_exception_signals(result)
        return result

    def _exception_result(self, operation: str, exc: Exception) -> ErrorResult:
        result = make_error_result(
            "other_exception",
            f"{type(exc).__name__}: {exc}",
            details={"operation": operation},
        )
        self._log_error_result(result)
        return result

    def _log_error_result(self, result: ErrorResult) -> None:
        category = str(result.get("error_type") or "other_exception")
        self.logger.log(category, str(result.get("message") or ""), dict(result.get("details") or {}))

    def _log_page_exception_signals(self, page_text: str) -> None:
        lowered = page_text.lower()
        if any(keyword in page_text for keyword in ("验证码", "安全验证", "滑块验证", "人机验证")) or "captcha" in lowered:
            self.logger.log("captcha", "Detected captcha signal in UI hierarchy", {"source": "uiautomate"})
        if any(
            keyword in page_text
            for keyword in ("优惠券", "红包", "弹窗", "广告", "立即领取", "以后再说", "暂不", "关闭")
        ):
            self.logger.log("popup", "Detected popup signal in UI hierarchy", {"source": "uiautomate"})


def create_event_hub_tools(event_hub: EventHub | None = None) -> list[StructuredTool]:
    """Create one input-only LangChain tool per recorded operation."""

    hub = event_hub or EventHub()
    return [_build_tool(spec, hub) for spec in TOOL_SPECS]


def build_tools() -> list[StructuredTool]:
    """Return the default recorded touch-operation tools."""

    return create_event_hub_tools()


def _build_tool(spec: ToolSpec, hub: EventHub) -> StructuredTool:
    operation = getattr(hub, spec.operation_name)
    tool_func = _wrap_operation(spec.name, spec.parameters, operation)
    return StructuredTool.from_function(
        func=tool_func,
        name=spec.name,
        description=spec.description,
    )


def _wrap_operation(name: str, parameters: tuple[str, ...], operation: Callable) -> Callable:
    if parameters == ("x", "y"):
        return _named(name, _xy_operation(operation))
    if parameters == ("package_name",):
        return _named(name, _package_operation(operation))
    if parameters == ():
        return _named(name, _zero_arg_operation(operation))
    return _named(name, _unsupported_operation(name, parameters))


def _xy_operation(operation: Callable[[int, int], str | ErrorResult]) -> Callable[[int, int], str | ErrorResult]:
    def tool_func(x: int, y: int) -> str | ErrorResult:
        return operation(x, y)

    return tool_func


def _package_operation(
    operation: Callable[[str], str | ErrorResult],
) -> Callable[[str], str | ErrorResult]:
    def tool_func(package_name: str) -> str | ErrorResult:
        return operation(package_name)

    return tool_func


def _zero_arg_operation(operation: Callable[[], str]) -> Callable[[], str]:
    def tool_func() -> str:
        return operation()

    return tool_func


def _unsupported_operation(name: str, parameters: tuple[str, ...]) -> Callable[[], ErrorResult]:
    def tool_func() -> ErrorResult:
        return make_error_result(
            "configuration_error",
            f"Unsupported tool parameter shape for {name}: {parameters}",
            details={"tool_name": name, "parameters": parameters},
        )

    return tool_func


def _named(name: str, func: Callable) -> Callable:
    func.__name__ = name
    return func
