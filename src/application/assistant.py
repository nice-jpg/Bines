"""Dialogue-level access to the application's device exploration agent."""

from langchain_core.language_models import BaseChatModel

from src.agent_hypervisor.root import RootAgent
from src.agent_runtime.contracts import AgentHandle
from src.conductor.engine import ConductorEngine


class AssistantEngine(RootAgent):
    def __init__(self, *, model: BaseChatModel, handle: AgentHandle, max_iterations: int = 1000) -> None:
        self._child_model = model
        super().__init__(model=model, handle=handle, max_iterations=max_iterations)

    def _system_prompt(self) -> str:
        return super()._system_prompt() + "\nAvailable agent type: conductor (application exploration)."

    def spawn_subagent(self, agent_type: str) -> dict[str, str]:
        """Create a persistent conductor child; send tasks separately with call."""
        if agent_type == "general":
            return super().spawn_subagent(agent_type)
        if agent_type != "conductor":
            raise ValueError(f"Unknown agent type: {agent_type!r}")
        with self.handle.spawner.child() as handle:
            engine = ConductorEngine(
                model=self._child_model, handle=handle, max_iterations=self._max_iterations
            )
            self.handle.spawner.start(engine)
        return {"subagent_id": handle.agent_id}

    def _create_fork(self, handle: AgentHandle) -> AssistantEngine:
        return AssistantEngine(model=self._child_model, handle=handle, max_iterations=self._max_iterations)
