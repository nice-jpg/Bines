"""LangChain tools for the Bines agent."""

from .common import create_common_tools
from .content_provider import create_content_provider_tools
from .event_hub import create_event_hub_tools
from .subagents import create_subagent_tools


def collect_tools(*, include_subagents: bool = False, subagent_manager=None) -> list:
    """Collect every LangChain tool managed by this package."""

    tools = [
        *create_common_tools(),
        *create_event_hub_tools(),
        *create_content_provider_tools(),
    ]
    if include_subagents:
        if subagent_manager is None:
            raise ValueError("subagent_manager is required when include_subagents=True")
        tools.extend(create_subagent_tools(subagent_manager))
    return tools


__all__ = ["collect_tools"]
