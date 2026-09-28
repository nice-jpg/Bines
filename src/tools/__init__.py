"""LangChain tools for the Bines agent."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .common import create_common_tools
from .event_hub import EventHub, create_conductor_event_hub_tools, create_event_hub_tools
from .navigation import create_navigation_tools
from .page_notes import create_page_notes_tool
try:
    from src.page_mechanism import PageMechanism
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from page_mechanism import PageMechanism

if TYPE_CHECKING:
    try:
        from src.collector import CollectorManager
    except ModuleNotFoundError:
        from collector import CollectorManager


def collect_conductor_tools(
    *,
    model=None,
    mechanism: PageMechanism | None = None,
    event_hub=None,
    collector_manager: CollectorManager | None = None,
) -> list:
    """Collect tools exposed to the conductor agent."""

    try:
        from src.collector import CollectorManager, create_prepare_collector_tool
    except ModuleNotFoundError:  # Supports running tests with src on sys.path.
        from collector import CollectorManager, create_prepare_collector_tool

    if collector_manager is not None:
        mechanism = mechanism or collector_manager.mechanism
        event_hub = event_hub or collector_manager.event_hub
    mechanism = mechanism or PageMechanism()
    event_hub = event_hub or EventHub()
    if collector_manager is not None and collector_manager.mechanism is not mechanism:
        raise ValueError("collector_manager must use the conductor PageMechanism instance.")
    if collector_manager is not None and collector_manager.event_hub is not event_hub:
        raise ValueError("collector_manager must use the conductor EventHub instance.")

    if collector_manager is None:
        collector_manager = CollectorManager(
            model=model,
            mechanism=mechanism,
            event_hub=event_hub,
        )
    return [
        *create_common_tools(),
        create_page_notes_tool(mechanism),
        *create_conductor_event_hub_tools(event_hub),
        *event_hub.page_bridge.create_tools(),
        *create_navigation_tools(event_hub=event_hub),
        *create_prepare_collector_tool(collector_manager),
    ]


def collect_tools(*, include_subagents: bool = False, subagent_manager=None, model=None) -> list:
    """Compatibility registry; conductor code should use collect_conductor_tools."""

    if not include_subagents:
        return collect_conductor_tools(model=model)

    from .content_provider import create_content_provider_tools
    from .subagents import create_subagent_tools

    tools = [
        *create_common_tools(include_legacy_manual=True),
        *create_event_hub_tools(),
        *create_content_provider_tools(),
    ]
    if include_subagents:
        if subagent_manager is None:
            raise ValueError("subagent_manager is required when include_subagents=True")
        tools.extend(create_subagent_tools(subagent_manager))
    return tools


__all__ = ["collect_conductor_tools", "collect_tools"]
