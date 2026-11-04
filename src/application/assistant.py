"""Dialogue-level access to the application's device exploration agent."""

from src.agent_hypervisor.root import RootAgent
from src.agent_runtime.contracts import AgentHandle
from src.conductor.engine import ConductorEngine
from src.configuration import get_model_instance


class AssistantEngine(RootAgent):
    def _system_prompt(self) -> str:
        return super()._system_prompt() + "\n" + self._prompt("assistant")

    def spawn_subagent(self, agent_type: str) -> dict[str, str]:
        """Create a persistent conductor child; send tasks separately with call."""
        if agent_type == "general":
            return super().spawn_subagent(agent_type)
        if agent_type != "conductor":
            raise ValueError(f"Unknown agent type: {agent_type!r}")
        with self.handle.spawner.child() as handle:
            engine = ConductorEngine(
                model=get_model_instance("behave"), handle=handle, max_iterations=self._max_iterations
            )
            self.handle.spawner.start(engine)
        return {"subagent_id": handle.agent_id}

    def _create_fork(self, handle: AgentHandle) -> AssistantEngine:
        return AssistantEngine(model=get_model_instance(), handle=handle, max_iterations=self._max_iterations)
