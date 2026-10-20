"""Explicit model confirmation of a freshly observed candidate page."""

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from langgraph.types import Command

from src.conductor.state import ConductorState
from src.strategy.general.visits import PageVisitController
from src.tools.shared.factory import create_tool


def create_confirm_page_tool(controller: PageVisitController) -> StructuredTool:
    def confirm_page(
        candidate_id: str,
        observation_id: str,
        matches: bool,
        evidence: str,
        runtime: ToolRuntime[None, ConductorState],
    ) -> Command[str]:
        """Confirm arrival using a fresh page/screenshot observation reference.

        Check actual destination and page structure against the candidate manual.
        A typo/abbreviation in the navigation merchant_name alone is not a mismatch:
        use matches=true if the intended destination is visibly correct and explain
        the typo in evidence. Merchant profile extraction belongs to collector;
        navigation labels are never required to equal its extracted fields.
        Wrong destination, wrong branch or wrong page state requires matches=false,
        followed by exit/retry navigation and a fresh confirmation. Do not accept a
        wrong page merely because its layout is also a merchant page.
        """
        if runtime.tool_call_id is None:
            raise ValueError("confirm_page requires a tool call ID")
        return controller.confirm_page(
            runtime.state, candidate_id, observation_id, matches, evidence, runtime.tool_call_id
        )

    return create_tool(confirm_page)
