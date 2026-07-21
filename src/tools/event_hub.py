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
    from src.tools.safety import DANGEROUS_TOOL_POLICY_METADATA_KEY
    from src.tools.tool_decorators import (
        dangerous_func,
        dangerous_policy_for,
        model_tool_callable,
        xy_operation,
    )
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from device.adapter import AndroidDevice
    from device.results import ErrorResult, is_error_result, make_error_result
    from device.translator import check_actions
    from tools.event_logger import WorkspaceEventLogger
    from tools.optimize_xml import optimize
    from tools.safety import DANGEROUS_TOOL_POLICY_METADATA_KEY
    from tools.tool_decorators import (
        dangerous_func,
        dangerous_policy_for,
        model_tool_callable,
        xy_operation,
    )

DEFAULT_POST_ACTION_UI_DELAY_SECONDS = 1.2


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    operation_name: str
    returns_xml: bool = False


TOOL_SPECS = (
    ToolSpec(
        "run_package",
        "Open an Android application by package name. This dangerous action requires a structured justification.",
        "run_package",
    ),
    ToolSpec(
        "close_package",
        "Force-stop an Android application by package name. This dangerous action requires a structured justification.",
        "close_package",
    ),
    ToolSpec(
        "tap",
        "Tap (x, y). Provide x/y coordinates. Returns the current UIAutomator XML hierarchy.",
        "tap",
        True,
    ),
    ToolSpec(
        "swipe_up",
        "swipe up from (x, y) for a short distance, screen will roll up. Use this tool to load more data below. Provide x/y coordinates. Returns current UIAutomator XML hierarchy.",
        "swipe_up",
        True,
    ),
    ToolSpec(
        "swipe_down",
        "swipe down from (x, y) for a short distance, screen will go down Use this tool to load more data from above, or to refresh current page. Provide x/y coordinates. Returns current UIAutomator XML hierarchy.",
        "swipe_down",
        True,
    ),
    ToolSpec(
        "swipe_back",
        "return to the last page. Returns current UIAutomator XML hierarchy.",
        "swipe_back",
        True,
    ),
    ToolSpec("uiautomate", "Get the current UIAutomator XML hierarchy.", "uiautomate", True),
    ToolSpec("screenshot", "Capture the current screen and return the remote image path.", "screenshot"),
)

EVENT_HUB_TOOL_NAMES = tuple(spec.name for spec in TOOL_SPECS)
XML_RESULT_TOOL_NAMES = frozenset(spec.name for spec in TOOL_SPECS if spec.returns_xml)
CONDUCTOR_EVENT_HUB_TOOL_NAMES = (
    "run_package",
    "close_package",
    "swipe_up",
    "swipe_down",
    "uiautomate",
    "screenshot",
)


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

    @xy_operation
    def tap(self, x: int, y: int) -> str | ErrorResult:
        """Replay the recorded tap action."""

        return self._replay_recorded_action("tap", x, y)

    @xy_operation
    def swipe_up(self, x: int, y: int) -> str | ErrorResult:
        """Replay the recorded upward swipe action."""

        return self._replay_recorded_action("swipe_up", x, y)

    @xy_operation
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

    @dangerous_func
    def run_package(self, package_name: str) -> str | ErrorResult:
        """Open the specified Android application package."""

        return self._run_device_operation("run_package", lambda: self.device.run_package(package_name))

    @dangerous_func
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


def create_event_hub_tools(
    event_hub: EventHub | None = None,
    *,
    tool_names: tuple[str, ...] | None = None,
) -> list[StructuredTool]:
    """Create one input-only LangChain tool per recorded operation."""

    hub = event_hub or EventHub()
    selected_names = frozenset(tool_names or EVENT_HUB_TOOL_NAMES)
    return [_build_tool(spec, hub) for spec in TOOL_SPECS if spec.name in selected_names]


def create_conductor_event_hub_tools(event_hub: EventHub | None = None) -> list[StructuredTool]:
    """Expose only non-ambiguous raw device operations to conductor."""

    return create_event_hub_tools(event_hub, tool_names=CONDUCTOR_EVENT_HUB_TOOL_NAMES)


def build_tools() -> list[StructuredTool]:
    """Return the default recorded touch-operation tools."""

    return create_event_hub_tools()


def _build_tool(spec: ToolSpec, hub: EventHub) -> StructuredTool:
    operation = _resolve_operation(spec.operation_name, hub)
    tool_func = model_tool_callable(operation)
    safety_policy = dangerous_policy_for(operation)
    metadata = (
        {DANGEROUS_TOOL_POLICY_METADATA_KEY: safety_policy}
        if safety_policy is not None
        else None
    )
    return StructuredTool.from_function(
        func=tool_func,
        name=spec.name,
        description=spec.description,
        metadata=metadata,
    )


def _resolve_operation(operation_name: str, hub: EventHub) -> Callable[..., object]:
    """Resolve only the operation requested by the current tool specification."""

    if operation_name == "run_package":
        return hub.run_package
    if operation_name == "close_package":
        return hub.close_package
    if operation_name == "tap":
        return hub.tap
    if operation_name == "swipe_up":
        return hub.swipe_up
    if operation_name == "swipe_down":
        return hub.swipe_down
    if operation_name == "swipe_back":
        return hub.swipe_back
    if operation_name == "uiautomate":
        return hub.uiautomate
    if operation_name == "screenshot":
        return hub.screenshot
    raise ValueError(f"Unsupported EventHub operation: {operation_name}")
