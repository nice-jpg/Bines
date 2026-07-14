"""Conductor page-document tools bound to the active page runtime."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

try:
    from src.page_mechanism import PageMechanism, PageRunHook
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from page_mechanism import PageMechanism, PageRunHook


class PageDocTool:
    """Read page mechanism docs and update the shared page runtime."""

    def __init__(
        self,
        *,
        mechanism: PageMechanism,
        page_run_hook: PageRunHook,
    ) -> None:
        if page_run_hook.mechanism is not mechanism:
            raise ValueError("PageDocTool mechanism and PageRunHook mechanism must be the same instance.")
        self.mechanism = mechanism
        self.page_run_hook = page_run_hook

    def set_current_page(self, current_path: str) -> str:
        """Set the canonical page path for automatic run.py execution."""

        return self.page_run_hook.set_current_path(current_path)

    def query_page_doc(self, current_path: str) -> str:
        """Return PAGE.md for the current canonical page path."""

        return self.mechanism.query_page_doc(current_path)

    def query_task_doc(self, current_path: str) -> str:
        """Return task.md for the current canonical page path."""

        return self.mechanism.query_task_doc(current_path)


def create_page_doc_tools(page_doc_tool: PageDocTool) -> list[StructuredTool]:
    """Create conductor page tools bound to one shared page runtime."""

    return [
        StructuredTool.from_function(
            func=page_doc_tool.set_current_page,
            name="set_current_page",
            description=(
                "Set the canonical current page path used by automatic run.py execution. "
                "Call this whenever entering or recognizing a new page. Input: current_path."
            ),
        ),
        StructuredTool.from_function(
            func=page_doc_tool.query_page_doc,
            name="query_page_doc",
            description="Read PAGE.md for the current canonical page path. Input: current_path.",
        ),
        StructuredTool.from_function(
            func=page_doc_tool.query_task_doc,
            name="query_task_doc",
            description="Read task.md for the current canonical page path. Input: current_path.",
        ),
    ]
