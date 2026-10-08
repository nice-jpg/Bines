"""Recorded device actions with Reco region views and internal XML artifacts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
import time

from langchain_core.tools import StructuredTool
from .page_bridge import PageBridge

if __package__ and __package__.startswith("src."):
    from src.device.adapter import AndroidDevice
    from src.device.results import ErrorResult, is_error_result, make_error_result
    from src.device.translator import check_actions
    from src.tools.event_logger import WorkspaceEventLogger
    from src.tools.operation_policy import (
        EXTERNAL_OPERATION_POLICY_METADATA_KEY,
        ExternalOperationPolicy,
    )
    from src.tools.operation_effect import (
        OPERATION_EFFECT_POLICY_METADATA_KEY,
        SCROLL_PAGE_CHANGE_POLICY,
        OperationEffectPolicy,
    )
    from src.tools.safety import DANGEROUS_TOOL_POLICY_METADATA_KEY
    from src.tools.tool_decorators import (
        dangerous_func,
        dangerous_policy_for,
        model_tool_callable,
        xy_operation,
    )
else:  # Supports running as: python src/run_agent.py
    from device.adapter import AndroidDevice
    from device.results import ErrorResult, is_error_result, make_error_result
    from device.translator import check_actions
    from tools.event_logger import WorkspaceEventLogger
    from tools.operation_policy import (
        EXTERNAL_OPERATION_POLICY_METADATA_KEY,
        ExternalOperationPolicy,
    )
    from tools.operation_effect import (
        OPERATION_EFFECT_POLICY_METADATA_KEY,
        SCROLL_PAGE_CHANGE_POLICY,
        OperationEffectPolicy,
    )
    from tools.safety import DANGEROUS_TOOL_POLICY_METADATA_KEY
    from tools.tool_decorators import (
        dangerous_func,
        dangerous_policy_for,
        model_tool_callable,
        xy_operation,
    )

DEFAULT_POST_ACTION_UI_DELAY_SECONDS = 1.2


class CollectionSwipe(StrEnum):
    LONG = "swipe_up_long"
    SHORT = "swipe_up"


@dataclass(frozen=True)
class SwipeObservation:
    """One device operation and its synchronized full snapshot."""
    hint: dict[str, Any]
    snapshot: dict[str, Any] | None
    change_ratio: float | None
    error: str = ""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    operation_name: str
    returns_page: bool = False
    notice_reason: str | None = None
    effect_policy: OperationEffectPolicy | None = None


TOOL_SPECS = (
    ToolSpec(
        "run_package",
        "Open an Android application by package name. This dangerous action requires a structured justification.",
        "run_package",
        notice_reason="Start the application probe task.",
    ),
    ToolSpec(
        "close_package",
        "Force-stop an Android application by package name. This dangerous action requires a structured justification.",
        "close_package",
        notice_reason="Complete the application probe task.",
    ),
    ToolSpec(
        "tap",
        "Tap (x, y). Provide x/y coordinates. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "tap",
        True,
    ),
    ToolSpec(
        "swipe_up",
        "Swipe up a short distance from (x, y) to load data below. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_up",
        True,
        effect_policy=SCROLL_PAGE_CHANGE_POLICY,
    ),
    ToolSpec(
        "swipe_up_long",
        "Swipe up a long distance from (x, y) to traverse a large collection region. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_up_long",
        True,
        effect_policy=SCROLL_PAGE_CHANGE_POLICY,
    ),
    ToolSpec(
        "swipe_to_random",
        "Swipe from (x, y) to a recorded random page state for collector training. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_to_random",
        True,
        effect_policy=SCROLL_PAGE_CHANGE_POLICY,
    ),
    ToolSpec(
        "swipe_down",
        "Swipe down a short distance from (x, y) to load data above or refresh. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_down",
        True,
        effect_policy=SCROLL_PAGE_CHANGE_POLICY,
    ),
    ToolSpec(
        "swipe_back",
        "Return to the last page. Returns page_change_hint search entrances; query their IDs for content (root view on first capture).",
        "swipe_back",
        True,
    ),
    ToolSpec("screenshot", "Capture the current screen and return a real image for visual target confirmation.", "screenshot"),
)

COLLECTOR_ONLY_EVENT_HUB_TOOL_NAMES = frozenset({"swipe_to_random"})
EVENT_HUB_TOOL_NAMES = tuple(
    spec.name for spec in TOOL_SPECS if spec.name not in COLLECTOR_ONLY_EVENT_HUB_TOOL_NAMES
)
CONDUCTOR_EVENT_HUB_TOOL_NAMES = (
    "run_package",
    "close_package",
    "swipe_up",
    "swipe_up_long",
    "swipe_down",
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
        self.page_bridge = PageBridge(self._dump_xml)
        self.operation_lock = self.page_bridge.operation_lock

    @xy_operation
    def tap(self, x: int, y: int) -> tuple:
        """Replay the recorded tap action."""

        return self._replay_recorded_action("tap", x, y)

    @xy_operation
    def swipe_up(self, x: int, y: int) -> tuple:
        """Replay the recorded upward swipe action."""

        return self._replay_recorded_action("swipe_up", x, y)

    @xy_operation
    def swipe_up_long(self, x: int, y: int) -> tuple:
        """Replay the recorded long upward swipe action."""

        return self._replay_recorded_action("swipe_up_long", x, y)

    @xy_operation
    def swipe_to_random(self, x: int, y: int) -> tuple:
        """Replay the recorded swipe used to select a random training state."""

        return self._replay_recorded_action("swipe_to_random", x, y)

    @xy_operation
    def swipe_down(self, x: int, y: int) -> tuple:
        """Replay the recorded downward swipe action."""

        return self._replay_recorded_action("swipe_down", x, y)

    def swipe_back(self) -> tuple:
        """Replay the recorded back action."""

        return self._replay_recorded_action("swipe_back", 0, 2000)

    def _dump_xml(self) -> str | ErrorResult:
        """Capture source XML for the internal Reco bridge."""
        result = self._run_device_operation("page_view", self.device.dump_ui, inspect_page=True)
        if type(result) is ErrorResult:
            return result
        assert(isinstance(result, str))
        return result

    def screenshot(self) -> str | ErrorResult:
        """Capture a screenshot on the device and return the remote path."""

        return self._run_device_operation("screenshot", self.device.screenshot)

    @dangerous_func
    def run_package(self, package_name: str) -> str | ErrorResult:
        """Open the specified Android application package."""

        result = self._run_device_operation("run_package", lambda: self.device.run_package(package_name))
        if not is_error_result(result):
            self.page_bridge.reset()
        return result

    @dangerous_func
    def close_package(self, package_name: str) -> str | ErrorResult:
        """Force-stop the specified Android application package."""

        return self._run_device_operation("close_package", lambda: self.device.close_package(package_name))

    def _replay_recorded_action(self, action_name: str, x: int, y: int) -> tuple:
        """Replay the action, then synchronize the shared abstract page view."""
        with self.operation_lock:
            return self._replay_and_refresh(action_name, x, y)

    def collection_swipe(self, action: CollectionSwipe, x: int, y: int) -> SwipeObservation:
        """Execute a collection gesture without invoking a model-facing tool."""
        with self.operation_lock:
            page, artifact = self._replay_and_refresh(action.value, x, y)
            if artifact is None:
                return SwipeObservation({}, None, None, "device_operation_failed")
            return SwipeObservation(page, artifact["page_snapshot"], self.page_bridge.root_change_ratio())

    def _replay_and_refresh(self, action_name: str, x: int, y: int) -> tuple:
        action_check_result = self._ensure_actions_checked()
        if is_error_result(action_check_result):
            return action_check_result, None
        action_result = self._run_device_operation(
            action_name,
            lambda: self.device.act(action_name=action_name, xy=(x, y)),
        )
        if is_error_result(action_result):
            return action_result, None
        self._wait_after_action()
        return self.page_bridge.refresh()

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
        with self.operation_lock:
            return self._execute_device_operation(name, operation, inspect_page)

    def _execute_device_operation(self, name: str, operation: Callable, inspect_page: bool) -> str | ErrorResult:
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
            self.logger.log("captcha", "Detected captcha signal in UI hierarchy", {"source": "page_view"})
        if any(
            keyword in page_text
            for keyword in ("优惠券", "红包", "弹窗", "广告", "立即领取", "以后再说", "暂不", "关闭")
        ):
            self.logger.log("popup", "Detected popup signal in UI hierarchy", {"source": "page_view"})


def create_event_hub_tools(
    event_hub: EventHub | None = None,
    *,
    tool_names: tuple[str, ...] | None = None,
) -> list[StructuredTool]:
    """Create one input-only LangChain tool per recorded operation."""

    hub = event_hub or EventHub()
    selected_names = frozenset(tool_names or EVENT_HUB_TOOL_NAMES)
    tools = [_build_tool(spec, hub) for spec in TOOL_SPECS if spec.name in selected_names]
    return tools + hub.page_bridge.create_tools() if tool_names is None else tools


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
    metadata = {}
    if spec.notice_reason is not None:
        metadata[EXTERNAL_OPERATION_POLICY_METADATA_KEY] = ExternalOperationPolicy(spec.notice_reason)
    if safety_policy is not None:
        metadata[DANGEROUS_TOOL_POLICY_METADATA_KEY] = safety_policy
    if spec.effect_policy is not None:
        metadata[OPERATION_EFFECT_POLICY_METADATA_KEY] = spec.effect_policy
    return StructuredTool.from_function(
        func=tool_func,
        name=spec.name,
        description=spec.description,
        metadata=metadata,
        response_format="content_and_artifact" if spec.returns_page else "content",
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
    if operation_name == "swipe_up_long":
        return hub.swipe_up_long
    if operation_name == "swipe_to_random":
        return hub.swipe_to_random
    if operation_name == "swipe_down":
        return hub.swipe_down
    if operation_name == "swipe_back":
        return hub.swipe_back
    if operation_name == "screenshot":
        from src.tools.screenshot import screenshot_content
        def screenshot() -> list:
            return screenshot_content(hub)
        return screenshot
    raise ValueError(f"Unsupported EventHub operation: {operation_name}")
