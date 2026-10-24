"""Factual probe progress exposed without a business completion gate."""

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

from src.common.utils.json import JsonObject
from src.conductor.progress import summarize_probe_progress
from src.conductor.state import ConductorState
from src.tools.shared.factory import create_tool


def create_progress_tool() -> StructuredTool:
    def get_probe_progress(runtime: ToolRuntime[None, ConductorState]) -> JsonObject:
        """Read visited targets and collection evidence. This report does not authorize or prohibit any action."""
        return summarize_probe_progress(runtime.state)

    return create_tool(get_probe_progress)
