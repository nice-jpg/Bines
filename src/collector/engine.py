"""Collector-specific graph construction and page-local execution."""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from src.agent_messages.types import InputMessage
from src.agent_runtime.contracts import AgentHandle, TaskResult
from src.agent_runtime.engine import AgentEngine
from src.agent_runtime.strategy.registration import StrategyRegistration
from src.agent_runtime.strategy.state import StrategyInput
from src.prompts import COLLECTOR_PROMPT
from src.strategy.collector.state import COLLECTOR, CollectorState
from src.strategy.collector.strategy import CollectorStrategy
from src.tools.communication.tools import create_talk_tool

from .state import CollectorAgentState
from .toolbox import CollectorToolbox
from .training import CollectorTrainingRegistry


class CollectorEngine(AgentEngine[CollectorAgentState]):
    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        toolbox: CollectorToolbox,
        session_id: str,
        max_iterations: int,
        handle: AgentHandle,
        registry: CollectorTrainingRegistry,
    ) -> None:
        self._toolbox: CollectorToolbox = toolbox
        self._registry = registry
        super().__init__(
            model=model,
            handle=handle,
            session_id=session_id,
            max_iterations=max_iterations,
        )

    def _prepare_report(self, result: TaskResult) -> TaskResult:
        if result.status not in {"completed", "failed"}:
            return result
        toolbox = self._toolbox
        restoration = toolbox.restore_to_top()
        verification = toolbox.training_result()
        profile = toolbox.merchant_profile()
        trained = result.status == "completed" and verification["ready"] and restoration["restored"]
        if result.status == "completed" and not trained:
            raise ValueError("Training and page restoration must pass before reporting completion")
        if trained:
            contract = toolbox.mechanism.collection_contract(toolbox.page_path)
            if contract is None:
                raise ValueError("Validated collector requires a collection contract")
            toolbox.persist_history()
            self._registry.mark_trained(
                toolbox.page_visit_id,
                toolbox.page_path,
                contract_fingerprint=contract.fingerprint,
                merchant_profile=profile,
                script_fingerprint=verification["script_fingerprint"],
                restoration=restoration,
            )
            swipe = verification["normal_swipe_profile"]
            if swipe is not None:
                self._registry.set_swipe_threshold(
                    toolbox.page_visit_id, swipe["change_threshold"], swipe["sample_count"]
                )
        else:
            self._registry.mark_untrained(toolbox.page_visit_id, toolbox.page_path)
        return result.model_copy(
            update={
                "data": {
                    "trained": trained,
                    "page_path": toolbox.page_path,
                    "page_visit_id": toolbox.page_visit_id,
                    "verification": verification,
                    "restoration": restoration,
                    "merchant_profile": profile,
                    "normal_swipe_profile": verification["normal_swipe_profile"],
                }
            }
        )

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

    def _input_state(self, messages: list[InputMessage]) -> StrategyInput:
        return {"messages": messages, "active_strategy": COLLECTOR}
