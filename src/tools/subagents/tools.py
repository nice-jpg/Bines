"""LangChain tools for synchronous subagent orchestration."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from src.subagents.manager import SubagentManager
from src.tools.shared.factory import create_tool


def create_subagent_tools(manager: SubagentManager) -> list[StructuredTool]:
    """Create subagent lifecycle tools for the main agent."""

    return [
        create_tool(
            func=manager.spawn_subagent,
            name="spawn_subagent",
            description=(
                "Create a synchronous child agent. Inputs: name, agent_type "
                "('independent' or 'delegated'), instructions, optional tool_names, "
                "optional max_iterations. Independent subagents solve with their own "
                "context. Delegated subagents receive a copy of the main agent runtime context. "
                "Keep instructions focused on the goal, known constraints, output requirements, "
                "and allowed tool scope; do not include merchant descriptions, product summaries, "
                "or page details that the subagent can obtain with page_view, "
                "or screenshot."
            ),
        ),
        create_tool(
            func=manager.call_subagent,
            name="call_subagent",
            description=(
                "Synchronously call an existing subagent and wait for its result before "
                "continuing. Inputs: agent_id and task. Returns subagent_result or subagent_error."
            ),
        ),
        create_tool(
            func=manager.kill_subagent,
            name="kill_subagent",
            description=(
                "Remove a subagent from the registry and discard its retained context. Input: agent_id."
            ),
        ),
    ]
