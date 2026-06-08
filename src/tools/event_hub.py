"""Touch-operation control hub exposed as a LangChain tool.

Each operation is fire-and-forget: the agent provides an operation name and a
random ``(x, y)`` coordinate, then the hub replays the matching recorded action.
The replay backend is intentionally left as a placeholder.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field


class TouchAction(str, Enum):
    TAP = "tap"
    SWIPE_UP = "swipe_up"
    SWIPE_DOWN = "swipe_down"
    SWIPE_LEFT = "swipe_left"
    SWIPE_RIGHT = "swipe_right"

    NOOP = "noop"


class EventHubInput(BaseModel):
    """Input-only schema for a recorded touch operation."""

    action: TouchAction = Field(description="Recorded operation to replay.")
    x: int = Field(description="Random x coordinate used to vary the replay.")
    y: int = Field(description="Random y coordinate used to vary the replay.")


@dataclass(frozen=True)
class EventCommand:
    """Input command consumed by the control hub."""

    action: TouchAction
    x: int
    y: int


class EventHub:
    """Central router for recorded touch-operation replay."""

    def handle(self, command: EventCommand) -> None:
        """Replay the recorded action for the requested operation."""

        self._replay_recorded_action(command)

    def _replay_recorded_action(self, command: EventCommand) -> None:
        """Replay a recorded action with coordinate jitter input.

        The concrete replay implementation is intentionally empty for now.
        Future code should map ``command.action`` to a recorded action asset and
        use ``command.x`` / ``command.y`` only as randomness input.
        """

        _ = command


def create_event_hub_tool(event_hub: EventHub | None = None) -> StructuredTool:
    """Create the input-only LangChain tool registered with the agent."""

    hub = event_hub or EventHub()

    def event_hub_tool(**kwargs: Any) -> None:
        parsed = EventHubInput(**kwargs)
        hub.handle(EventCommand(**parsed.model_dump()))

    return StructuredTool.from_function(
        func=event_hub_tool,
        name="event_hub",
        description=(
            "Fire-and-forget control hub for recorded touch operations. "
            "Choose an action and provide a random x/y coordinate. The tool "
            "does not return operation data."
        ),
        args_schema=EventHubInput,
    )


def build_tools() -> list[StructuredTool]:
    """Return the default touch-control tools for agent registration."""

    return [create_event_hub_tool()]
