"""Engine ownership, public API and thread lifecycle."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
import inspect
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from src.agent_runtime.engine import AgentEngine
from src.agent_runtime.coordinator import AgentCoordinator
from src.agent_runtime.worker import AgentWorker
from src.agent_runtime.types.execution import EngineResult, EngineStatus
from src.conductor.engine import ConductorEngine
from src.channel.messages import InBoundMessage
from engine_helpers import ScriptedEngine, Model


def test_engine_owns_session_and_keeps_multiple_conversations(monkeypatch):
    from gateway_helpers import GatewayHandle, RecordingChannel

    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    model = Model(replies=[AIMessage(content="answer")])
    engine = ScriptedEngine(model=model, handle=gateway.handle)
    for text in ["first", "second"]:
        channel.receive("one", text)
        assert engine.run_turn().output == "answer"
    channel.receive("two", "other")
    assert engine.run_turn().output == "answer"
    assert {session for _, session in engine.conversation.conversations} == {"one", "two"}
    assert engine.conversation.route == {"agent_id": gateway.handle.agent_id}
    assert len(model.seen) == 3
    other = ScriptedEngine(model=model, handle=gateway.handle)
    assert engine.conversation is not other.conversation
    engine.close()
    with pytest.raises(Exception, match="closed"):
        engine.conversation.read_input(wait=False)
    assert gateway.receive_messages(wait=False) == []


def test_engine_exposes_only_execution_methods():
    methods = {
        name for name, value in AgentEngine.__dict__.items() if callable(value) and not name.startswith("_")
    }
    assert methods == {"run_turn", "continue_turn", "resume_turn", "arun_turn", "acontinue_turn", "aresume_turn", "close", "send_message", "call", "report", "kill", "spawn_subagent", "report_interruption"}
    for method in {"run_turn", "continue_turn", "resume_turn", "close"}:
        assert list(inspect.signature(getattr(AgentEngine, method)).parameters) == ["self"]


def test_engine_has_no_thread_ownership_or_execution_lock():
    engine = ScriptedEngine(model=Model(replies=[AIMessage(content="ok")]))
    assert "_owner" not in vars(engine)
    assert "_lock" not in vars(engine)


def test_invalid_transitions_rejected_before_graph_call():
    engine = ScriptedEngine(model=Model(replies=[AIMessage(content="ok")]))
    with pytest.raises(RuntimeError, match="continuation"):
        engine.continue_turn()
    with pytest.raises(RuntimeError, match="interrupted"):
        engine.resume_turn()


def test_worker_repeats_calls_then_closes_on_same_thread():
    import threading

    calls = []
    statuses = iter([EngineStatus.COMPLETED, EngineStatus.INTERRUPTED, EngineStatus.STOPPED])
    coordinator = Mock()
    from src.agent_runtime.types.execution import InterruptKind

    def run():
        calls.append(threading.get_ident())
        status = next(statuses)
        return EngineResult(
            status, interrupt_kind=InterruptKind.HUMAN if status == EngineStatus.INTERRUPTED else None
        )

    from unittest.mock import AsyncMock
    coordinator.arun = AsyncMock(side_effect=run)
    coordinator.close.side_effect = lambda: calls.append(threading.get_ident())
    worker = AgentWorker(coordinator)
    worker.start()
    assert worker.join().status == EngineStatus.STOPPED
    assert len(set(calls)) == 1 and calls[0] != threading.get_ident()
    with pytest.raises(RuntimeError):
        worker.start()


def test_worker_propagates_exception_after_cleanup():
    coordinator = Mock()
    from unittest.mock import AsyncMock
    coordinator.arun = AsyncMock(side_effect=ValueError("failed"))
    worker = AgentWorker(coordinator)
    worker.start()
    with pytest.raises(ValueError, match="failed"):
        worker.join()
    coordinator.close.assert_called_once()


def test_probe_preparation_only_uses_current_messages(monkeypatch):
    import src.conductor.engine as module

    monkeypatch.setattr(
        module, "build_app_probe_messages", lambda: [{"role": "user", "content": "environment"}]
    )
    engine = ScriptedEngine(model=Model(replies=[AIMessage(content="ok")]))
    assert (
        engine._prepare_messages([{"role": "user", "content": "执行应用探测任务"}])[0]["content"]
        == "environment"
    )
    assert engine._prepare_messages([{"role": "user", "content": "normal"}]) == [
        {"role": "user", "content": "normal"}
    ]
    assert engine._input_state([{"role": "user", "content": "执行应用探测任务"}])["probe"]["run_id"]


def test_conductor_preserves_human_payload():
    from src.agent_runtime.types.execution import InterruptKind

    engine = ConductorEngine.__new__(ConductorEngine)
    assert engine._resume_payload(InterruptKind.HUMAN, "done") == {
        "decisions": [{"type": "respond", "message": "done"}]
    }


def test_initial_input_hook_is_lazy_and_not_repeated_after_completion():
    class TaskEngine(ScriptedEngine):
        def _create_initial_messages(self):
            self.initial_calls += 1
            return [{"role": "user", "content": self.assignment}]

    model = Model(replies=[AIMessage(content="answer")])
    engine = TaskEngine(model=model)
    # Fields deliberately become available only after construction.
    engine.initial_calls = 0
    engine.assignment = "late task"
    assert "initial_messages" not in inspect.signature(AgentEngine).parameters
    assert "_initial" not in vars(engine)
    assert "_input_middleware" not in vars(engine)
    assert engine.run_turn().status == EngineStatus.COMPLETED
    assert engine.initial_calls == 1
    assert any(message.content == "late task" for message in model.seen[0])
    engine.assignment = "must not be injected"
    engine.test_channel.receive("one", "next request")
    assert engine.run_turn().status == EngineStatus.COMPLETED
    assert engine.initial_calls == 1
    assert not any(message.content == "must not be injected" for message in model.seen[1])
    engine.close()


def test_default_initial_input_hook_uses_conversation():
    class ConversationEngine(ScriptedEngine):
        _create_initial_messages = AgentEngine._create_initial_messages

    model = Model(replies=[AIMessage(content="answer")])
    engine = ConversationEngine(model=model)
    engine.test_channel.receive("one", "channel task")
    assert engine.run_turn().status == EngineStatus.COMPLETED
    assert any("channel task" in message.content for message in model.seen[0])
    engine.close()
