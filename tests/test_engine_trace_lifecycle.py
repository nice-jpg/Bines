"""Trace boundaries follow execution origin, not the model message role."""

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from engine_helpers import Model, ScriptedEngine
from gateway_helpers import GatewayHandle, RecordingChannel
from nTrace.storage import TraceStorage
from src.agent_runtime.types.execution import EngineStatus, InterruptKind


class Sink:
    def __init__(self, storage):
        self.storage = storage
        self.events = []

    def emit(self, event):
        self.events.append(event)
        self.storage.put_events([event])
        return True


@pytest.fixture
def sink():
    storage = TraceStorage(":memory:")
    try:
        yield Sink(storage)
    finally:
        storage.close()


def engine(sink, **kwargs):
    result = ScriptedEngine(model=Model(replies=[AIMessage(content="done")]), **kwargs)
    result._trace.client = sink
    return result


def test_child_assignments_merge_into_parent_tool_call(sink):
    children = []

    @tool
    def collect() -> str:
        """Run two internal collector assignments."""
        for _ in range(2):
            child = engine(sink, name="collector", initial_messages=[{"role": "user", "content": "assignment"}])
            children.append(child)
            assert child.run_turn().status == EngineStatus.COMPLETED
        return "collected"

    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    parent = ScriptedEngine(name="conductor", tools=[collect], handle=gateway.handle,
        model=Model(replies=[AIMessage(content="", tool_calls=[{"id": "c", "name": "collect", "args": {}}]),
                             AIMessage(content="done")]))
    parent._trace.client = sink
    channel.receive("one", "collect")
    assert parent.run_turn().status == EngineStatus.COMPLETED
    assert len(sink.storage.list_traces()) == 1
    trace = sink.storage.get_trace(parent._trace.trace_id)
    assert len(trace["agents"]) == 3
    assert all(agent["parent_agent_id"] == 1 for agent in trace["agents"][1:])
    assert all(not child._trace.current_binding.user_input_boundary for child in children)
    assert len({child._trace.trace_id for child in children}) == 2


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("internal", [False, True])
def test_continuation_and_recovery_retain_binding(sink, resume, internal):
    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    agent = engine(sink, handle=gateway.handle,
                   initial_messages=[{"role": "user", "content": "assignment"}] if internal else [])
    if not internal:
        channel.receive("one", "start")
    agent.run_turn()
    binding = agent._trace.current_binding
    if resume:
        agent._execution.status = EngineStatus.INTERRUPTED
        agent._execution.interrupt_kind = InterruptKind.MODEL_TRANSPORT
        agent.resume_turn()
    else:
        agent._execution.status = EngineStatus.CONTINUE_REQUIRED
        agent.continue_turn()
    assert agent._trace.current_binding is binding
    assert binding.user_input_boundary is (not internal)
    assert len(sink.storage.list_traces()) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
def test_user_turns_in_same_session_retain_root(sink, asynchronous):
    import asyncio
    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    agent = engine(sink, handle=gateway.handle)
    ids = []
    for text in ("first", "second"):
        channel.receive("one", text)
        if asynchronous:
            asyncio.run(agent.arun_turn())
        else:
            agent.run_turn()
        ids.append(agent._trace.trace_id)
        assert agent._trace.current_binding.user_input_boundary
    assert ids[0] == ids[1]
    assert len(sink.storage.list_traces()) == 1
    agent._checkpoint_id = "new-session"
    channel.receive("one", "new session")
    if asynchronous:
        asyncio.run(agent.arun_turn())
    else:
        agent.run_turn()
    assert agent._trace.trace_id != ids[0]
    assert len(sink.storage.list_traces()) == 2


def test_missing_recovery_binding_is_logged_and_isolated(sink, caplog):
    agent = engine(sink, initial_messages=[{"role": "user", "content": "assignment"}])
    agent.run_turn()
    agent._trace.clear()
    agent._execution.status = EngineStatus.CONTINUE_REQUIRED
    agent.continue_turn()
    assert agent._trace.current_binding.user_input_boundary
    assert "Trace binding missing" in caplog.text


@pytest.mark.parametrize("asynchronous", [False, True])
def test_mailbox_tasks_and_reports_keep_the_invocation_tree(sink, asynchronous):
    """Children start after the call returned, even after another root ran."""
    import asyncio

    def run(agent):
        return asyncio.run(agent.arun_turn()) if asynchronous else agent.run_turn()

    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    parent = engine(sink, name="conductor", handle=gateway.handle)
    child_id = gateway.host.router.register_child(gateway.agent_id)
    child = engine(sink, name="collector", handle=gateway.host.handle(child_id))
    child._model.replies = [AIMessage(content="", tool_calls=[{
        "id": "report", "name": "report", "args": {
            "task_id": "collect", "result": {"status": "blocked", "summary": "need input"}
        }
    }]), AIMessage(content="waiting")]

    channel.receive("one", "start")
    run(parent)
    root = parent._trace.trace_id
    parent.call(child_id, "collect", "assignment")
    # A temporally closer, unrelated root must not become this child's parent.
    other_channel = RecordingChannel()
    other = engine(sink, handle=GatewayHandle(other_channel).handle)
    other_channel.receive("other", "unrelated")
    run(other)
    run(child)
    binding = child._trace.current_binding
    assert not binding.user_input_boundary
    trace = sink.storage.get_trace(root)
    assert [agent["agent_name"] for agent in trace["agents"]] == ["conductor", "collector"]
    assert trace["agents"][1]["parent_agent_id"] == 1

    run(parent)  # child's blocked report is not a new user request
    assert parent._trace.trace_id == root
    parent.call(child_id, "collect", "clarification")
    child._model.replies = [AIMessage(content="waiting")]
    from src.agent_runtime.contracts import TaskResult
    child._model.on_call = lambda _: child.report("collect", TaskResult(status="blocked", summary="waiting"))
    run(child)
    assert child._trace.current_binding is binding
    assert len(sink.storage.list_traces()) == 2

    grandchild_id = gateway.host.router.register_child(child_id)
    grandchild = engine(sink, name="grandchild", handle=gateway.host.handle(grandchild_id))
    child.call(grandchild_id, "nested", "nested assignment")
    run(grandchild)
    trace = sink.storage.get_trace(root)
    assert trace["agents"][2]["parent_agent_id"] == trace["agents"][1]["agent_id"]

    # Follow-up user input keeps both the root and the worker's existing lane.
    channel.receive("one", "next user task")
    run(parent)
    next_root = parent._trace.trace_id
    assert next_root == root
    parent.call(child_id, "another", "new assignment")
    child._model.on_call = None
    run(child)
    assert child._trace.current_binding is binding
    assert len(sink.storage.get_trace(root)["agents"]) == 3
