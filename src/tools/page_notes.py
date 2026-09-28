"""Restricted maintenance of the active page's reusable operation notes."""

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool


def create_page_notes_tool(mechanism):
    def update_page_notes(content: str, runtime: ToolRuntime) -> dict:
        """Replace only the current PAGE.md Operation Observations with reusable navigation notes."""
        path = runtime.state.get("page_context_path")
        if not path:
            raise ValueError("No active page")
        return {"ok": True, "updated": mechanism.update_page_notes(path, content)}
    return StructuredTool.from_function(update_page_notes)
