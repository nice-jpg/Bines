"""Capture completed model/tool boundaries without copying runtime resources."""

from copy import deepcopy

from langchain.agents.middleware import AgentMiddleware, AgentState
from langgraph.runtime import Runtime


class ForkContext(AgentMiddleware):
    def __init__(self) -> None:
        self._state: dict[str, object] = {}

    def before_model(self, state: AgentState, runtime: Runtime) -> None:
        self._state = dict(state)

    async def abefore_model(self, state: AgentState, runtime: Runtime) -> None:
        self.before_model(state, runtime)

    def snapshot(self) -> dict[str, object]:
        return deepcopy(self._state)
