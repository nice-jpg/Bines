"""LangChain tools for synchronous subagent orchestration."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

try:
    from src.subagents_manager import SubagentManager
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from subagents import SubagentManager


def create_subagent_tools(manager: SubagentManager) -> list[StructuredTool]:
    """Create subagent lifecycle tools for the main agent."""

    return [
        StructuredTool.from_function(
            func=manager.spawn_subagent,
            name="spawn_subagent",
            description=(
                "Create a synchronous child agent. Inputs: name, agent_type "
                "('independent' or 'delegated'), instructions, optional tool_names, "
                "optional max_iterations. Independent subagents solve with their own "
                "context. Delegated subagents receive a copy of the main agent runtime context."
            ),
        ),
        StructuredTool.from_function(
            func=manager.call_subagent,
            name="call_subagent",
            description=(
                "Synchronously call an existing subagent and wait for its result before "
                "continuing. Inputs: agent_id and task. Returns subagent_result or subagent_error."
            ),
        ),
        StructuredTool.from_function(
            func=manager.kill_subagent,
            name="kill_subagent",
            description=(
                "Remove a subagent from the registry and discard its retained context. "
                "Input: agent_id."
            ),
        ),
    ]


def build_tools(manager: SubagentManager) -> list[StructuredTool]:
    """Return default subagent lifecycle tools."""

    return create_subagent_tools(manager)
