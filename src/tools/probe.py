"""Explicit model-selected entry into the configured application probe."""

from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from src.conductor.state import ConductorState, initial_probe_state
from src.prompts import build_initial_messages, build_probe_task_spec
from src.strategy.general.state import GENERAL, initial_general_state
from src.strategy.merchant.state import initial_merchant_state
from src.tools.shared.factory import create_tool


def create_start_probe_tool() -> StructuredTool:
    def start_app_probe(runtime: ToolRuntime[None, ConductorState]) -> Command:
        """Start the configured app exploration/merchant collection task when requested by the user or parent.

        Call before probe device actions. Returns task configuration and initializes
        probe state. Repeated calls preserve an existing probe and its progress.
        Do not call for ordinary conversation or merely discussing a probe.
        """
        if runtime.state["probe"].get("run_id"):
            return Command(update={"messages": [ToolMessage(
                content="Probe already initialized. Continue the existing task and progress.",
                tool_call_id=runtime.tool_call_id,
            )]})
        spec = build_probe_task_spec()
        context = "\n\n".join(build_initial_messages())
        update = initial_probe_state(spec.target_leaf_paths)
        update["probe"]["package_name"] = spec.package_name
        update["active_strategy"] = GENERAL
        update["general"] = initial_general_state()
        update["merchant"] = initial_merchant_state()
        update["messages"] = [ToolMessage(content=context, tool_call_id=runtime.tool_call_id)]
        return Command(update=dict(update))

    return create_tool(start_app_probe)
