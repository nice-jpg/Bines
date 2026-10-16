"""Semantic page interaction tools for the conductor agent."""

from __future__ import annotations

from typing import Final, Literal

from langchain_core.tools import StructuredTool

from src.device import DeviceOperationService, PageOperationResult
from src.tool_factory import create_tool
from src.tools.operation_effect import (
    NAVIGATION_PAGE_CHANGE_POLICY,
    OPERATION_EFFECT_POLICY_METADATA_KEY,
)

NAVIGATION_TOOL_NAMES: Final[tuple[str, str, str]] = ("tap_in_page", "navigate_to", "navigate_back")


class NavigationTools:
    """Bind business navigation semantics to the low-level device actions."""

    def __init__(self, *, event_hub: DeviceOperationService) -> None:
        self.event_hub: DeviceOperationService = event_hub

    def tap_in_page(
        self, x: int, y: int, purpose: Literal["page_interaction", "dismiss_overlay"] = "page_interaction"
    ) -> PageOperationResult:
        """Tap a control that is guaranteed to remain on the current business page."""

        return self.event_hub.tap(x, y)

    def navigate_to(
        self, x: int, y: int, next_path: str, merchant_name: str = "", recovery_reason: str = ""
    ) -> PageOperationResult:
        """Tap a control that enters the declared absolute canonical child page."""

        # ProbeControlMiddleware owns canonical-path validation.
        del next_path, merchant_name
        return self.event_hub.tap(x, y)

    def navigate_back(self, next_path: str, recovery_reason: str = "") -> PageOperationResult:
        """Return to the declared absolute canonical parent or prior page."""

        del next_path
        return self.event_hub.swipe_back()


def create_navigation_tools(
    *,
    event_hub: DeviceOperationService,
) -> list[StructuredTool]:
    navigation: NavigationTools = NavigationTools(event_hub=event_hub)
    return [
        create_tool(
            func=navigation.tap_in_page,
            response_format="content_and_artifact",
            name="tap_in_page",
            description=(
                "Tap a visible control that stays on the current business page, such as a filter, "
                "sort option, tab, expand control, or popup close button. Never use it to enter or "
                "leave a business page. Set purpose=dismiss_overlay only when you judge this tap dismisses "
                "an overlay; otherwise use page_interaction. Returns page_change_hint search entrances "
                "to query by ID, not page content (root view on first capture)."
            ),
        ),
        create_tool(
            func=navigation.navigate_to,
            response_format="content_and_artifact",
            name="navigate_to",
            description=(
                "Tap a visible target that enters another business page. Provide x, y, and next_path. "
                "next_path must be the complete canonical path from the app home, for example "
                "meituan/外卖/美食/商家. The target manual is validated before the device is touched. "
                "For merchant destinations, merchant_name is required: copy the full currently visible name including branch. "
                "Previously completed merchants are rejected before tapping, regardless of list order or coordinates. "
                "If leaving an unfinished merchant for recovery, provide a concrete recovery_reason; this does not mark it complete. "
                "Returns page_change_hint search entrances to query by ID, not page content (root view on first capture)."
            ),
            metadata={
                OPERATION_EFFECT_POLICY_METADATA_KEY: NAVIGATION_PAGE_CHANGE_POLICY,
            },
        ),
        create_tool(
            func=navigation.navigate_back,
            response_format="content_and_artifact",
            name="navigate_back",
            description=(
                "Return to a declared prior business page. Provide its complete canonical next_path, "
                "for example meituan/外卖/美食. The target manual is validated before the device is touched. "
                "For recovery from an unfinished merchant, provide recovery_reason; revisit and complete it later. "
                "Returns page_change_hint search entrances to query by ID, not page content (root view on first capture)."
            ),
        ),
    ]


__all__: list[str] = ["NAVIGATION_TOOL_NAMES", "NavigationTools", "create_navigation_tools"]
