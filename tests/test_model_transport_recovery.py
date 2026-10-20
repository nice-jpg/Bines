"""Transport/recursion recovery preserves the checkpoint invocation form."""

from types import SimpleNamespace
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from websockets.exceptions import ConnectionClosedError
from src.agent_runtime.coordinator import AgentCoordinator
from src.agent_runtime.types.execution import EngineStatus, InterruptKind
from engine_helpers import ScriptedEngine, Model


@pytest.mark.parametrize(
    "error,kind",
    [
        (ConnectionClosedError(None, None, None), InterruptKind.MODEL_TRANSPORT),
        (GraphRecursionError("limit"), InterruptKind.RECURSION),
        (ValueError("invalid config"), InterruptKind.EXECUTION),
    ],
)
def test_failures_preserve_checkpoint_and_resume_without_replaying_input(error, kind):
    calls = []
    engine = ScriptedEngine(
        model=Model(replies=[AIMessage(content="unused")]),
        initial_messages=[HumanMessage(content="probe")],
        session_id="session",
    )

    def invoke(value, config, *, durability):
        assert durability == "exit"
        assert config["configurable"]["thread_id"] == "session"
        calls.append(value)
        if len(calls) == 1:
            raise error
        return {"messages": [HumanMessage(content="preserved"), AIMessage(content="resumed")]}

    engine.agent = SimpleNamespace(
        invoke=invoke,
        get_state=lambda config: SimpleNamespace(values={"messages": [HumanMessage(content="preserved")]}),
    )
    first = engine.run_turn()
    assert first.interrupt_kind == kind
    assert engine._state["messages"][0].content == "preserved"
    if kind == InterruptKind.EXECUTION:
        with pytest.raises(RuntimeError, match="confirmed"):
            engine.resume_turn()
        engine._execution.status = EngineStatus.RESUME_READY
        engine._execution.resume_input = "继续"
    assert engine.resume_turn().output == "resumed"
    assert calls[1] is None


def test_permanent_failure_is_not_automatically_retried():
    model = Model(
        replies=[AIMessage(content="unused")], on_call=lambda _: (_ for _ in ()).throw(ValueError("invalid"))
    )
    engine = ScriptedEngine(model=model, initial_messages=[HumanMessage(content="probe")])
    assert AgentCoordinator(engine).run().interrupt_kind == InterruptKind.EXECUTION
    assert len(model.seen) == 1
