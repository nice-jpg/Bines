"""Semantic page interaction tools for the conductor agent."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import StructuredTool

if __package__ and __package__.startswith("src."):
    from src.tools.event_hub import EventHub
    from src.tools.operation_effect import (
        NAVIGATION_PAGE_CHANGE_POLICY,
        OPERATION_EFFECT_POLICY_METADATA_KEY,
    )
else:  # Supports running tests with src on sys.path.
    from tools.event_hub import EventHub
    from tools.operation_effect import (
        NAVIGATION_PAGE_CHANGE_POLICY,
        OPERATION_EFFECT_POLICY_METADATA_KEY,
    )


NAVIGATION_TOOL_NAMES = ("tap_in_page", "navigate_to", "navigate_back")


class NavigationTools:
    """Bind business navigation semantics to the low-level device actions."""

    def __init__(self, *, event_hub: EventHub) -> None:
        self.event_hub = event_hub

    def tap_in_page(self, x: int, y: int) -> Any:
        """Tap a control that is guaranteed to remain on the current business page."""

        return self.event_hub.tap(x, y)

    def navigate_to(self, x: int, y: int, next_path: str) -> Any:
        """Tap a control that enters the declared absolute canonical child page."""

        # ProbeControlMiddleware owns canonical-path validation.
        del next_path
        return self.event_hub.tap(x, y)

    def navigate_back(self, next_path: str) -> Any:
        """Return to the declared absolute canonical parent or prior page."""

        del next_path
        return self.event_hub.swipe_back()


def create_navigation_tools(
    *,
    event_hub: EventHub,
) -> list[StructuredTool]:
    navigation = NavigationTools(event_hub=event_hub)
    return [
        StructuredTool.from_function(
            func=navigation.tap_in_page,
            name="tap_in_page",
            description=(
                "Tap a visible control that stays on the current business page, such as a filter, "
                "sort option, tab, expand control, or popup close button. Never use it to enter or "
                "leave a business page. Returns current UIAutomator XML."
            ),
        ),
        StructuredTool.from_function(
            func=navigation.navigate_to,
            name="navigate_to",
            description=(
                "Tap a visible target that enters another business page. Provide x, y, and next_path. "
                "next_path must be the complete canonical path from the app home, for example "
                "meituan/外卖/美食/商家. The target manual is validated before the device is touched."
            ),
            metadata={
                OPERATION_EFFECT_POLICY_METADATA_KEY: NAVIGATION_PAGE_CHANGE_POLICY,
            },
        ),
        StructuredTool.from_function(
            func=navigation.navigate_back,
            name="navigate_back",
            description=(
                "Return to a declared prior business page. Provide its complete canonical next_path, "
                "for example meituan/外卖/美食. The target manual is validated before the device is touched."
            ),
        ),
    ]


__all__ = ["NAVIGATION_TOOL_NAMES", "NavigationTools", "create_navigation_tools"]
