"""Fixed inter-agent tool surface; identities come from the owning engine."""

from typing import TYPE_CHECKING

from langchain_core.tools import BaseTool

from src.tools.shared.factory import create_tool

if TYPE_CHECKING:
    from src.agent_runtime.engine import AgentEngine


def communication_tools(engine: AgentEngine) -> list[BaseTool]:
    return [
        create_tool(func)
        for func in (
            engine.spawn_subagent,
            engine.call,
            engine.report,
            engine.kill,
        )
    ]
