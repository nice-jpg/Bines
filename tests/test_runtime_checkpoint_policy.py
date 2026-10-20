"""Exit-only checkpoints preserve continuation without per-step snapshots."""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.types import Command, interrupt

from src.agent_runtime.types.execution import EngineStatus, InterruptKind
from engine_helpers import ScriptedEngine, run_engine
from test_runtime_communication import Model


def checkpoints(runtime, thread):
    return list(runtime.agent.get_state_history({"configurable": {"thread_id": thread}}))


def test_many_steps_save_only_at_invoke_exit_and_preserve_history():
    @tool
    def work() -> str:
        """Perform one unit of work."""
        return "done"

    model = Model(
        replies=[
            *[
                AIMessage(content="", tool_calls=[{"name": "work", "args": {}, "id": f"w{i}"}])
                for i in range(4)
            ],
            AIMessage(content="finished"),
        ]
    )
    runtime = ScriptedEngine(name="test", model=model, tools=[work], system_prompt="base")
    result = run_engine(runtime, [HumanMessage(content="start")], session_id="a")
    assert result.status == EngineStatus.COMPLETED
    assert len(checkpoints(runtime, "a")) == 1
    assert len(runtime._state["messages"]) == 10
    run_engine(runtime, [HumanMessage(content="next")], session_id="a")
    assert len(checkpoints(runtime, "a")) == 2
    assert any(m.content == "start" for m in model.seen[-1])


@pytest.mark.parametrize("exit_kind", ["error", "interrupt"])
def test_child_exit_preserves_pending_node_without_replaying_finished_tool(exit_kind):
    calls = []

    @tool
    def work() -> str:
        """Perform one side effect."""
        calls.append("work")
        return "done"

    @tool
    def approval() -> str:
        """Request human approval."""
        return str(interrupt("approve"))

    replies = [AIMessage(content="", tool_calls=[{"name": "work", "args": {}, "id": "w"}])]
    if exit_kind == "interrupt":
        replies.append(AIMessage(content="", tool_calls=[{"name": "approval", "args": {}, "id": "a"}]))
    replies.append(AIMessage(content="finished"))
    model = Model(replies=replies)

    def fail(index):
        if exit_kind == "error" and index == 1:
            raise ValueError("model failure")

    model.on_call = fail
    runtime = ScriptedEngine(name="child", model=model, tools=[work, approval], system_prompt="base")
    outcome = run_engine(runtime, [HumanMessage(content="start")], session_id="child")
    assert outcome.status == EngineStatus.INTERRUPTED
    assert len(checkpoints(runtime, "child")) == 1
    # Simulate the already-validated human recovery boundary.
    runtime._execution.status = EngineStatus.RESUME_READY
    runtime._execution.resume_input = "yes"
    result = runtime.resume_turn()
    assert result.output == "finished"
    assert calls == ["work"]
    assert len(checkpoints(runtime, "child")) == 2
