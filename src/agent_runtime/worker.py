"""One application agent thread; no messaging or execution policy."""

from threading import Thread

from .coordinator import AgentCoordinator
from .types.execution import EngineResult, EngineStatus


class AgentWorker:
    def __init__(self, coordinator: AgentCoordinator) -> None:
        self._coordinator: AgentCoordinator = coordinator
        self._thread: Thread = Thread(target=self._run, name="agent-worker")
        self._result: EngineResult | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        self._thread.start()

    def join(self) -> EngineResult:
        self._thread.join()
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise RuntimeError("Agent thread exited without a result")
        return self._result

    def _run(self) -> None:
        try:
            try:
                while True:
                    self._result = self._coordinator.run()
                    if self._result.status == EngineStatus.STOPPED:
                        break
            finally:
                self._coordinator.close()
        except BaseException as error:
            self._error = error
