"""Restricted maintenance of the active page's reusable operation notes."""

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

from src.conductor.state import ConductorState, current_visit
from src.page.mechanism import PageMechanism
from src.tools.shared.factory import create_tool


def create_page_notes_tool(mechanism: PageMechanism) -> StructuredTool:
    def update_page_notes(content: str, runtime: ToolRuntime[None, ConductorState]) -> dict[str, bool]:
        """Replace only the current PAGE.md Operation Observations with reusable navigation notes."""
        path: str | None = current_visit(runtime.state).get('path')
        if not path:
            raise ValueError("No active page")
        return {"ok": True, "updated": mechanism.update_page_notes(path, content)}

    return create_tool(update_page_notes)
