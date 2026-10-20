"""Execution policy, independent of message reception and session selection."""

from langchain.agents.middleware.types import AgentState

from .engine import AgentEngine
from .types.execution import EngineResult, EngineStatus, InterruptKind


class AgentCoordinator[StateT: AgentState = AgentState]:
    def __init__(self, engine: AgentEngine[StateT]) -> None:
        self._engine: AgentEngine[StateT] = engine
        self._closed: bool = False

    def run(self) -> EngineResult:
        if self._closed:
            raise RuntimeError("AgentCoordinator is closed")
        result: EngineResult = self._engine.run_turn()
        while True:
            if result.status == EngineStatus.CONTINUE_REQUIRED:
                result = self._engine.continue_turn()
            elif result.status == EngineStatus.RESUME_READY or (
                result.status == EngineStatus.INTERRUPTED
                and result.interrupt_kind
                in {
                    InterruptKind.LIVENESS,
                    InterruptKind.MODEL_TRANSPORT,
                    InterruptKind.RECURSION,
                }
            ):
                result = self._engine.resume_turn()
            else:
                if result.output and "conversation_id" in self._engine.conversation.route:
                    self._engine.conversation.send_message(result.output)
                return result

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._engine.close()
