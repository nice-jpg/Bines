"""LangChain tools for the Bines agent."""

from .content_provider import create_content_provider_tools
from .event_hub import create_event_hub_tools


def collect_tools() -> list:
    """Collect every LangChain tool managed by this package."""

    return [
        *create_event_hub_tools(),
        *create_content_provider_tools(),
    ]


__all__ = ["collect_tools"]
