"""Real-graph boundary, acknowledgement and child input regressions."""

from src.agent_runtime.middleware.communication import ConversationInputMiddleware, read_pending_input

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from src.agent_runtime.coordinator import AgentCoordinator
from src.agent_runtime.types.execution import EngineStatus, InterruptKind
from communication_helpers import InputChannel, QueueContext
from engine_helpers import Model, ScriptedEngine
from src.application.gateway_manager import GatewayManager
from gateway_helpers import GatewayHandle
from src.tools.communication.tools import create_talk_tool


def test_queue_fifo_checkpoint_ack():
    inbox = QueueContext()
    inbox.receive("first")
    inbox.receive("second")
    batch = read_pending_input(inbox.engine.conversation, {})
    assert len(batch) == 1
    assert "user: first\nuser: second" in batch[0].content
    assert read_pending_input(inbox.engine.conversation, {}) == batch
    assert read_pending_input(inbox.engine.conversation, {"messages": batch}) == []
    inbox.close()
    assert read_pending_input(inbox.engine.conversation, {"messages": batch}) == []
    assert inbox.engine.conversation.input_closed


def make_engine(model, tools=()):
    channel = InputChannel()
    gateway = GatewayHandle(channel)
    engine = ScriptedEngine(model=model, tools=tools, gateway_id=gateway.gateway_id)
    channel.receive("start")
    return engine, channel


def test_terminal_arrival_reenters_same_checkpoint_without_duplicate_user():
    model = Model(replies=[AIMessage(content="first"), AIMessage(content="updated")])
    engine, channel = make_engine(model)
    model.on_call = lambda index: channel.receive("new information") if index == 0 else None
    result = AgentCoordinator(engine).run()
    assert result.output == "updated"
    assert [m.content.splitlines()[-1] for m in model.seen[1] if isinstance(m, HumanMessage)] == ["user: start", "user: new information"]
    assert sum("user: new information" in m.content for m in engine._state["messages"]) == 1
    assert not read_pending_input(engine.conversation, engine._state)


def test_engines_sharing_gateway_do_not_copy_messages_or_history():
    child_model = Model(replies=[AIMessage(content="child first"), AIMessage(content="child done")])
    child_states = []

    def delegate() -> str:
        """Run an isolated child."""
        child = ScriptedEngine(
            model=child_model,
            initial_messages=[HumanMessage(content="child assignment")],
            gateway_id=parent.gateway_id,
        )
        result = child.run_turn()
        while result.status == EngineStatus.CONTINUE_REQUIRED:
            result = child.continue_turn()
        child_states.append(child._state)
        child.close()
        return result.output

    model = Model(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "delegate", "args": {}, "id": "d"}]),
            AIMessage(content="parent done"),
        ]
    )
    parent, channel = make_engine(model, [StructuredTool.from_function(delegate)])
    child_model.on_call = lambda index: channel.receive("fix baseline") if index == 0 else None
    result = AgentCoordinator(parent).run()
    assert result.output == "parent done"
    assert any("user: fix baseline" in m.content for m in child_model.seen[-1])
    assert any(m.content == "child assignment" for m in child_model.seen[-1])
    assert child_states[0]["active_strategy"] == "general"
    assert not any("fix baseline" in m.content for m in model.seen[-1])
    assert not any(m.content == "child assignment" for m in model.seen[-1])


def test_talk_tool_hidden_runtime_and_no_interrupt():
    tool = create_talk_tool(lambda message: True)
    assert set(tool.tool_call_schema.model_json_schema()["properties"]) == {"message"}
    model = Model(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "talk", "args": {"message": "problem"}, "id": "t"}]),
            AIMessage(content="done"),
        ]
    )
    engine, channel = make_engine(model)
    assert engine.run_turn().status == EngineStatus.COMPLETED
    manager = GatewayManager.instance()
    manager.start(engine.gateway_id)
    manager.finish_output(engine.gateway_id)
    manager.join(engine.gateway_id)
    assert [m.text for m in channel.sent] == ["problem"]


def test_async_injection_matches_sync():
    inbox = QueueContext()
    inbox.receive("supplement")
    middleware = ConversationInputMiddleware(inbox.engine.conversation)
    context = SimpleNamespace(context=None)
    state = {"messages": []}
    assert asyncio.run(middleware.abefore_model(state, context)) == middleware.before_model(state, context)


def test_input_during_tool_preserves_protocol_order():
    def operation() -> str:
        """Perform one operation."""
        channel.receive("new direction")
        return "operation completed"

    model = Model(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "operation", "args": {}, "id": "o"}]),
            AIMessage(content="done"),
        ]
    )
    engine, channel = make_engine(model, [StructuredTool.from_function(operation)])
    assert engine.run_turn().status == EngineStatus.COMPLETED
    messages = engine._state["messages"]
    assert next(i for i, m in enumerate(messages) if isinstance(m, ToolMessage)) < next(
        i for i, m in enumerate(messages) if "user: new direction" in m.content
    )


def test_failed_model_resume_does_not_duplicate_injected_message():
    model = Model(replies=[AIMessage(content="done")])
    engine, channel = make_engine(model)
    channel.receive("supplement")

    def fail(index):
        if index == 0:
            raise ValueError("model configuration failed")

    model.on_call = fail
    assert engine.run_turn().interrupt_kind == InterruptKind.EXECUTION
    channel.receive("继续")
    assert engine.run_turn().status == EngineStatus.RESUME_READY
    result = engine.resume_turn()
    while result.status == EngineStatus.CONTINUE_REQUIRED:
        result = engine.continue_turn()
    assert result.status == EngineStatus.COMPLETED
    assert sum("user: supplement" in m.content for m in engine._state["messages"]) == 1


def test_shutdown_after_generation_does_not_dispatch_new_tool():
    calls = []

    def operation() -> str:
        """Perform one operation."""
        calls.append(True)
        return "done"

    model = Model(replies=[AIMessage(content="", tool_calls=[{"name": "operation", "args": {}, "id": "o"}])])
    engine, channel = make_engine(model, [StructuredTool.from_function(operation)])
    model.on_call = lambda _: GatewayManager.instance().stop(engine.gateway_id)
    assert engine.run_turn().status == EngineStatus.STOPPED
    assert not calls


def test_unbound_talk_fails_explicitly():
    model = Model(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "talk", "args": {"message": "problem"}, "id": "t"}]),
            AIMessage(content="done"),
        ]
    )
    engine = ScriptedEngine(model=model, initial_messages=[HumanMessage(content="start")])
    assert engine.run_turn().status == EngineStatus.COMPLETED
    assert next(m for m in engine._state["messages"] if isinstance(m, ToolMessage)).status == "error"


def test_long_tool_observes_shutdown_without_consuming_input():
    engine, channel = make_engine(Model(replies=[AIMessage(content="done")]))
    assert engine._tool_yield_reason({}) == "user_message_pending"
    assert engine.conversation.pending_count == 1
    GatewayManager.instance().stop(engine.gateway_id)
    assert engine._tool_yield_reason({}) == "shutdown_requested"


def test_confirmation_arriving_during_failed_model_remains_schedulable():
    model = Model(replies=[AIMessage(content="recovered")])
    engine, channel = make_engine(model)

    def fail_once(index):
        if index == 0:
            channel.receive("继续")
            raise ValueError("configuration changed")

    model.on_call = fail_once
    coordinator = AgentCoordinator(engine)
    assert coordinator.run().interrupt_kind == InterruptKind.EXECUTION
    assert engine.conversation.pending_count == 1
    assert coordinator.run().output == "recovered"


def test_shutdown_during_failed_model_does_not_wait_for_recovery():
    model = Model(replies=[AIMessage(content="unused")])
    engine, channel = make_engine(model)

    def fail(index):
        GatewayManager.instance().stop(engine.gateway_id)
        raise ValueError("failure while closing")

    model.on_call = fail
    assert AgentCoordinator(engine).run().status == EngineStatus.STOPPED


def test_first_arrival_during_seeded_execution_binds_subsequent_tool_notifications():

    channel = InputChannel()
    gateway = GatewayHandle(channel)

    def notify() -> str:
        """Send from a tool after the first incoming communication."""
        assert engine.send_message("late-bound notice")
        return "sent"

    model = Model(replies=[
        AIMessage(content="", tool_calls=[{"name": "notify", "args": {}, "id": "n"}]),
        AIMessage(content="done"),
    ])
    model.on_call = lambda index: channel.receive("first communication") if index == 0 else None
    engine = ScriptedEngine(model=model, tools=[StructuredTool.from_function(notify)],
                            initial_messages=[HumanMessage(content="assignment")], gateway_id=gateway.gateway_id)
    result = engine.run_turn()
    assert result.status == EngineStatus.COMPLETED
    gateway.start()
    gateway.finish_output()
    gateway.join()
    assert [m.text for m in channel.sent] == ["late-bound notice"]
    assert any("user: first communication" in m.content for m in model.seen[-1])


def test_engine_send_requires_its_own_bound_conversation():
    import pytest
    first, second = QueueContext(), QueueContext()
    first.receive("bind")
    first.engine.conversation.read_input(wait=False)
    assert first.engine.send_message("inside")
    with pytest.raises(RuntimeError, match="bound address"):
        second.engine.send_message("unbound")


def test_async_notifications_use_each_engines_own_route():
    first, second = QueueContext(), QueueContext()
    for inbox in (first, second):
        inbox.receive("bind")
        inbox.engine.conversation.read_input(wait=False)
    async def send(inbox, text):
        await asyncio.sleep(0)
        assert inbox.engine.send_message(text)
    async def run():
        await asyncio.gather(send(first, "one"), send(second, "two"))
    asyncio.run(run())
    for inbox, expected in ((first, "one"), (second, "two")):
        inbox.gateway.start()
        inbox.gateway.finish_output()
        inbox.gateway.join()
        assert [message.text for message in inbox.channel.sent] == [expected]
