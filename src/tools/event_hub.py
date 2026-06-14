"""Recorded touch operations exposed as individual LangChain tools.

Each operation is fire-and-forget: the agent calls a specific tool with a
random ``(x, y)`` coordinate, then the hub replays the matching recorded action.
The replay backend is intentionally left as a placeholder.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from langchain_core.tools import StructuredTool

try:
    from src.device.adapter import AndroidDevice
    from src.device.translator import check_actions
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from device.adapter import AndroidDevice
    from device.translator import check_actions

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    operation_name: str
    parameters: tuple[str, ...]


TOOL_SPECS = (
    ToolSpec("run_package", "Open an Android application by package name.", "run_package", ("package_name",)),
    ToolSpec("tap", "tap (x, y). Provide x/y coordinates. Returns no operation data.", "tap", ("x", "y")),
    ToolSpec(
        "swipe_up",
        "swipe up from (x, y) for a short distance, screen will roll up. Use this tool to load more data below. Provide x/y coordinates. Returns no operation data.",
        "swipe_up",
        ("x", "y"),
    ),
    ToolSpec(
        "swipe_down",
        "swipe down from (x, y) for a short distance, screen will go down Use this tool to load more data from above, or to refresh current page. Provide x/y coordinates. Returns no operation data.",
        "swipe_down",
        ("x", "y"),
    ),
    ToolSpec(
        "swipe_back",
        "return to the last page. Provide x/y coordinates. Returns no operation data.",
        "swipe_back",
        ("x", "y"),
    ),
    ToolSpec("uiautomate", "Get the current UIAutomator XML hierarchy.", "uiautomate", ()),
    ToolSpec("screenshot", "Capture the current screen and return the remote image path.", "screenshot", ()),
)

EVENT_HUB_TOOL_NAMES = tuple(spec.name for spec in TOOL_SPECS)


class EventHub:
    """Central owner for recorded touch-operation replay."""

    def __init__(self) -> None:
        self.device = AndroidDevice()
        self._actions_checked = False

    def tap(self, x: int, y: int) -> None:
        """Replay the recorded tap action."""

        self._replay_recorded_action("tap", x, y)

    def swipe_up(self, x: int, y: int) -> None:
        """Replay the recorded upward swipe action."""

        self._replay_recorded_action("swipe_up", x, y)

    def swipe_down(self, x: int, y: int) -> None:
        """Replay the recorded downward swipe action."""

        self._replay_recorded_action("swipe_down", x, y)

    def swipe_back(self, x: int, y: int) -> None:
        """Replay the recorded back action."""

        self._replay_recorded_action("swipe_back", x, y)

    def uiautomate(self) -> str:
        """Return the current UIAutomator XML hierarchy."""

        return self.device.dump_ui()

    def screenshot(self) -> str:
        """Capture a screenshot on the device and return the remote path."""

        return self.device.screenshot()

    def run_package(self, package_name: str) -> str:
        """Open the specified Android application package."""

        return self.device.run_package(package_name)

    def _replay_recorded_action(self, action_name: str, x: int, y: int) -> None:
        """Replay a recorded action with coordinate jitter input.

        The concrete replay implementation is intentionally empty for now.
        Future code should map ``action_name`` to a recorded action asset and
        use ``x`` / ``y`` only as randomness input.
        """
        self._ensure_actions_checked()
        print('%s on (%d, %d)' % (action_name, x, y))
        self.device.act(action_name = action_name, xy = (x, y))

    def _ensure_actions_checked(self) -> None:
        if self._actions_checked:
            return
        check_actions(self.device)
        self._actions_checked = True


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
    raise ValueError(f"Unsupported tool parameter shape for {name}: {parameters}")


def _xy_operation(operation: Callable[[int, int], None]) -> Callable[[int, int], None]:
    def tool_func(x: int, y: int) -> None:
        return operation(x, y)

    return tool_func


def _package_operation(operation: Callable[[str], str]) -> Callable[[str], str]:
    def tool_func(package_name: str) -> str:
        return operation(package_name)

    return tool_func


def _zero_arg_operation(operation: Callable[[], str]) -> Callable[[], str]:
    def tool_func() -> str:
        return operation()

    return tool_func


def _named(name: str, func: Callable) -> Callable:
    func.__name__ = name
    return func
