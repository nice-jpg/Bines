"""One application agent thread; no messaging or execution policy."""

import asyncio
from concurrent.futures import Future
from threading import Thread

from langchain.agents.middleware.types import AgentState

from .coordinator import AgentCoordinator
from .types.execution import EngineResult, EngineStatus


class AgentWorker[StateT: AgentState = AgentState]:
    def __init__(self, coordinator: AgentCoordinator[StateT]) -> None:
        self._coordinator: AgentCoordinator[StateT] = coordinator
        self._thread: Thread = Thread(target=self._run, name="agent-worker")
        self._result: EngineResult | None = None
        self._error: BaseException | None = None
        self._finished: Future[EngineResult] = Future()

    def start(self) -> None:
        self._thread.start()

    def join(self) -> EngineResult:
        self._thread.join()
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise RuntimeError("Agent thread exited without a result")
        return self._result

    async def wait(self) -> EngineResult:
        return await asyncio.shield(asyncio.wrap_future(self._finished))

    def _run(self) -> None:
        asyncio.run(self._run_async())

    async def _run_async(self) -> None:
        try:
            try:
                while True:
                    self._result = await self._coordinator.arun()
                    if self._result.status == EngineStatus.STOPPED:
                        break
            finally:
                self._coordinator.close()
        except BaseException as error:
            self._error = error
            self._finished.set_exception(error)
        else:
            if self._result is None:
                self._finished.set_exception(RuntimeError("Agent exited without a result"))
            else:
                self._finished.set_result(self._result)
