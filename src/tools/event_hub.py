"""Recorded touch operations exposed as individual LangChain tools.

Each operation is fire-and-forget: the agent calls a specific tool with a
random ``(x, y)`` coordinate, then the hub replays the matching recorded action.
The replay backend is intentionally left as a placeholder.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.tools import StructuredTool

try:
    from src.device.adapter import AndroidDevice
    from src.device.translator import check_actions
except ModuleNotFoundError:  # Supports running as: python src/run_agent.py
    from device.adapter import AndroidDevice
    from device.translator import check_actions

EVENT_HUB_TOOL_NAMES = (
    "tap",
    "swipe_up",
    "swipe_down",
    "swipe_back",
    "screenshot",
    "uiautomate",
)


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

    def _replay_recorded_action(self, action_name: str, x: int, y: int) -> None:
        """Replay a recorded action with coordinate jitter input.

        The concrete replay implementation is intentionally empty for now.
        Future code should map ``action_name`` to a recorded action asset and
        use ``x`` / ``y`` only as randomness input.
        """
        self._ensure_actions_checked()
        self.device.act(action_name = action_name, xy = (x, y))
        # return self.device.dump_ui()

    def _ensure_actions_checked(self) -> None:
        if self._actions_checked:
            return
        check_actions(self.device)
        self._actions_checked = True


def create_event_hub_tools(event_hub: EventHub | None = None) -> list[StructuredTool]:
    """Create one input-only LangChain tool per recorded operation."""

    hub = event_hub or EventHub()
    return [
        _make_tool("tap", "tap (x, y).", hub.tap),
        _make_tool("swipe_up", "swipe up from (x, y) for a short distance.", hub.swipe_up),
        _make_tool("swipe_down", "swipe down from (x, y) for a short distance.", hub.swipe_down),
        _make_tool("swipe_back", "return to the last page.", hub.swipe_back),
        _make_zero_arg_tool("uiautomate", "Get the current UIAutomator XML hierarchy.", hub.uiautomate),
        _make_zero_arg_tool("screenshot", "Capture the current screen and return the remote image path.", hub.screenshot),
    ]


def build_tools() -> list[StructuredTool]:
    """Return the default recorded touch-operation tools."""

    return create_event_hub_tools()


def _make_tool(name: str, description: str, operation: Callable[[int, int], None]) -> StructuredTool:
    def tool_func(x: int, y: int) -> None:
        operation(x, y)

    tool_func.__name__ = name
    return StructuredTool.from_function(
        func=tool_func,
        name=name,
        description=f"{description} Provide random x/y coordinates. Returns no operation data.",
    )


def _make_zero_arg_tool(name: str, description: str, operation: Callable[[], str]) -> StructuredTool:
    def tool_func() -> str:
        return operation()

    tool_func.__name__ = name
    return StructuredTool.from_function(
        func=tool_func,
        name=name,
        description=description,
    )
