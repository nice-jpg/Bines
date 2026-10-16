"""Agent-selected reporting tool; the caller supplies its message capability."""

from collections.abc import Callable

from langchain_core.tools import StructuredTool

from src.tools.shared.factory import create_tool


def create_talk_tool(send_message: Callable[[str], bool]) -> StructuredTool:
    def talk(message: str) -> bool:
        """Report a problem without pausing or waiting for a reply."""
        return send_message(message)

    return create_tool(talk)
