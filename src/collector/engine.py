"""Collector-specific graph construction and page-local execution."""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from src.agent_messages.types import InputMessage
from src.agent_runtime.engine import AgentEngine
from src.agent_runtime.strategy.registration import StrategyRegistration
from src.prompts import COLLECTOR_PROMPT
from src.strategy.collector.state import COLLECTOR, CollectorState
from src.strategy.collector.strategy import CollectorStrategy
from src.tools.communication.tools import create_talk_tool

from .state import CollectorAgentState
from .toolbox import CollectorToolbox


class CollectorEngine(AgentEngine[CollectorAgentState]):
    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        toolbox: CollectorToolbox,
        assignment: str,
        session_id: str,
        max_iterations: int,
        gateway_id: str,
    ) -> None:
        self._toolbox: CollectorToolbox = toolbox
        self._assignment: str = assignment
        super().__init__(
            model=model,
            gateway_id=gateway_id,
            session_id=session_id,
            max_iterations=max_iterations,
        )

    def _create_initial_messages(self) -> Sequence[InputMessage]:
        return [{"role": "user", "content": self._assignment}]

    def _name(self) -> str:
        return "collector"

    def _system_prompt(self) -> str:
        return COLLECTOR_PROMPT

    def _create_tools(self) -> Sequence[BaseTool]:
        return [*self._toolbox.create_tools(), create_talk_tool(self.send_message)]

    def _initial_collector_state(self) -> CollectorState:
        return {
            "page_path": self._toolbox.page_path,
            "page_visit_id": self._toolbox.page_visit_id,
            "run_id": self._toolbox.run_id,
        }

    def _strategy_registrations(self) -> Sequence[StrategyRegistration[CollectorAgentState]]:
        return [
            StrategyRegistration(
                name=COLLECTOR,
                strategy=CollectorStrategy[CollectorAgentState](self._initial_collector_state()),
                state_schema=CollectorState,
                initial_state=self._initial_collector_state,
            )
        ]

    def _strategy_state_schema(self) -> type[CollectorAgentState]:
        return CollectorAgentState

    def _select_strategy(self, state: CollectorAgentState) -> str:
        return COLLECTOR
