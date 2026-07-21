"""Semantic page interaction tools for the conductor agent."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

try:
    from src.page_mechanism import PageMechanism, normalize_page_path
    from src.tools.event_hub import EventHub
    from src.tools.operation_policy import (
        EXTERNAL_OPERATION_POLICY_METADATA_KEY,
        ExternalOperationPolicy,
    )
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from page_mechanism import PageMechanism, normalize_page_path
    from tools.event_hub import EventHub
    from tools.operation_policy import (
        EXTERNAL_OPERATION_POLICY_METADATA_KEY,
        ExternalOperationPolicy,
    )


NAVIGATION_TOOL_NAMES = ("tap_in_page", "navigate_to", "navigate_back")
PAGE_CONTEXT_APP_ROOT = "page_context_app_root"
PAGE_CONTEXT_PATH = "page_context_path"


class NavigationTools:
    """Bind business navigation semantics to the low-level device actions."""

    def __init__(self, *, mechanism: PageMechanism, event_hub: EventHub) -> None:
        self.mechanism = mechanism
        self.event_hub = event_hub

    def tap_in_page(self, x: int, y: int) -> Any:
        """Tap a control that is guaranteed to remain on the current business page."""

        return self.event_hub.tap(x, y)

    def navigate_to(self, x: int, y: int, next_path: str, runtime: ToolRuntime) -> Any:
        """Tap a control that enters the declared absolute canonical child page."""

        self._validate_target(next_path, runtime, allow_same=False)
        return self.event_hub.tap(x, y)

    def navigate_back(self, next_path: str, runtime: ToolRuntime) -> Any:
        """Return to the declared absolute canonical parent or prior page."""

        self._validate_target(next_path, runtime, allow_same=False)
        return self.event_hub.swipe_back()

    def _validate_target(self, next_path: str, runtime: ToolRuntime, *, allow_same: bool) -> str:
        state = runtime.state if isinstance(runtime.state, Mapping) else {}
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        app_root = str(state.get(PAGE_CONTEXT_APP_ROOT) or "")
        parts, error = normalize_page_path(next_path)
        canonical_path = "/".join(parts)
        if error or not canonical_path:
            raise ValueError(error or "next_path is required.")
        if not app_root or canonical_path.split("/", 1)[0] != app_root:
            raise ValueError(
                f"next_path must be absolute from the current app root {app_root!r}: "
                f"use {app_root!r} or {app_root!r}/..."
            )
        if not allow_same and canonical_path == current_path:
            raise ValueError("next_path must differ from the current page; use tap_in_page for same-page actions.")
        return self.mechanism.validate_page_manual(canonical_path)


def create_navigation_tools(
    *,
    mechanism: PageMechanism,
    event_hub: EventHub,
) -> list[StructuredTool]:
    navigation = NavigationTools(mechanism=mechanism, event_hub=event_hub)
    return [
        StructuredTool.from_function(
            func=navigation.tap_in_page,
            name="tap_in_page",
            description=(
                "Tap a visible control that stays on the current business page, such as a filter, "
                "sort option, tab, expand control, or popup close button. Never use it to enter or "
                "leave a business page. Returns current UIAutomator XML."
            ),
            metadata={
                EXTERNAL_OPERATION_POLICY_METADATA_KEY: ExternalOperationPolicy(
                    "Operate a visible control on the current page."
                )
            },
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
                EXTERNAL_OPERATION_POLICY_METADATA_KEY: ExternalOperationPolicy(
                    "Navigate to the declared business page.",
                    next_path_argument="next_path",
                )
            },
        ),
        StructuredTool.from_function(
            func=navigation.navigate_back,
            name="navigate_back",
            description=(
                "Return to a declared prior business page. Provide its complete canonical next_path, "
                "for example meituan/外卖/美食. The target manual is validated before the device is touched."
            ),
            metadata={
                EXTERNAL_OPERATION_POLICY_METADATA_KEY: ExternalOperationPolicy(
                    "Return to the declared business page.",
                    next_path_argument="next_path",
                )
            },
        ),
    ]


__all__ = ["NAVIGATION_TOOL_NAMES", "NavigationTools", "create_navigation_tools"]
