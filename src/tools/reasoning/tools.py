"""Reasoning tool without external side effects."""

from langchain_core.tools import StructuredTool

from src.tools.shared.factory import create_tool


class ThinkingTool:
    """No-op scratchpad tool for reflecting on complex tool results."""

    def think(self, thought: str) -> str:
        """Record a thought without changing external state or fetching data."""

        if not str(thought or "").strip():
            return "No thought was recorded because the input was empty."
        return "Thought recorded."


def create_thinking_tool() -> StructuredTool:
    return create_tool(
        ThinkingTool().think,
        description="Reflect on complex tool outputs. This does not fetch new information, operate devices or write files. Input: thought.",
    )
