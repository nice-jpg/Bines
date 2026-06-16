"""Common LangChain tools shared by the Bines agent."""

from __future__ import annotations

from langchain_core.tools import StructuredTool


class ThinkingTool:
    """No-op scratchpad tool for reflecting on complex tool results."""

    def think(self, thought: str) -> str:
        """Record a thought without changing external state or fetching data."""

        if not str(thought or "").strip():
            return "No thought was recorded because the input was empty."
        return "Thought recorded."


def create_common_tools(thinking_tool: ThinkingTool | None = None) -> list[StructuredTool]:
    """Create shared reasoning tools for the LangChain agent."""

    tool = thinking_tool or ThinkingTool()
    return [
        StructuredTool.from_function(
            func=tool.think,
            name="think",
            description=(
                "Use this tool as a private scratchpad after receiving complex tool outputs. "
                "It does not fetch new information, operate the device, write files, or change "
                "any external state. Use it to analyze the latest result, check whether required "
                "information is complete, identify risks or contradictions, and plan the next "
                "device or output action. Input: thought."
            ),
        )
    ]


def build_tools() -> list[StructuredTool]:
    """Return the default common tools."""

    return create_common_tools()
