"""Message injection uses the same queue before and during graph execution."""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from engine_helpers import Model, ScriptedEngine
from communication_helpers import InputChannel, QueueContext
from gateway_helpers import GatewayHandle
from src.agent_runtime.coordinator import AgentCoordinator
from src.agent_runtime.middleware.communication import ConversationInputMiddleware, read_pending_input
from src.agent_runtime.types.execution import EngineStatus, InterruptKind


def make_engine(model, tools=()):
    channel = InputChannel()
    gateway = GatewayHandle(channel)
    engine = ScriptedEngine(model=model, tools=tools, handle=gateway.handle)
    channel.receive("start")
    return engine, channel


def test_inbox_acknowledges_only_checkpointed_message_block():
    inbox = QueueContext()
    inbox.receive("first")
    inbox.receive("second")
    batch = read_pending_input(inbox.engine.conversation, {})
    assert len(batch) == 1
    assert inbox.engine.conversation.pending_block.source_texts == ("first", "second")
    assert read_pending_input(inbox.engine.conversation, {}) == batch
    assert read_pending_input(inbox.engine.conversation, {"messages": batch}) == []
    inbox.close()
    assert read_pending_input(inbox.engine.conversation, {"messages": batch}) == []
    assert inbox.engine.conversation.input_closed


def test_arrival_at_final_boundary_continues_same_checkpoint():
    model = Model(replies=[AIMessage(content="first"), AIMessage(content="updated")])
    engine, channel = make_engine(model)
    model.on_call = lambda index: channel.receive("new information") if index == 0 else None
    assert AgentCoordinator(engine).run().output == "updated"
    assert sum('"text": "new information"' in m.content for m in engine._state["messages"]) == 1
    assert not read_pending_input(engine.conversation, engine._state)


def test_async_injection_matches_sync():
    inbox = QueueContext()
    inbox.receive("supplement")
    middleware = ConversationInputMiddleware(inbox.engine.conversation)
    assert asyncio.run(middleware.abefore_model({"messages": []}, None)) == middleware.before_model({"messages": []}, None)


def test_input_during_tool_preserves_protocol_pairing():
    def operation() -> str:
        """Finish an operation while new input arrives."""
        channel.receive("new direction")
        return "complete"
    model = Model(replies=[AIMessage(content="", tool_calls=[{
        "name": "operation", "args": {}, "id": "o",
    }]), AIMessage(content="done")])
    engine, channel = make_engine(model, [StructuredTool.from_function(operation)])
    assert engine.run_turn().status == EngineStatus.COMPLETED
    messages = engine._state["messages"]
    assert next(i for i, m in enumerate(messages) if isinstance(m, ToolMessage)) < next(
        i for i, m in enumerate(messages) if '"text": "new direction"' in m.content
    )


def test_failed_model_resume_does_not_duplicate_inputs():
    model = Model(replies=[AIMessage(content="done")])
    engine, channel = make_engine(model)
    channel.receive("supplement")
    def fail(index):
        if index == 0:
            raise ValueError("configuration failed")
    model.on_call = fail
    assert engine.run_turn().interrupt_kind == InterruptKind.EXECUTION
    channel.receive("继续")
    assert engine.run_turn().status == EngineStatus.RESUME_READY
    result = engine.resume_turn()
    while result.status == EngineStatus.CONTINUE_REQUIRED:
        result = engine.continue_turn()
    assert result.status == EngineStatus.COMPLETED
    assert sum('"text": "supplement"' in m.content for m in engine._state["messages"]) == 1


def test_shutdown_after_generation_prevents_tools():
    calls = []
    def operation() -> str:
        """Record a device operation."""
        calls.append(True)
        return "done"
    model = Model(replies=[AIMessage(content="", tool_calls=[{"name": "operation", "args": {}, "id": "o"}])])
    engine, channel = make_engine(model, [StructuredTool.from_function(operation)])
    model.on_call = lambda _: engine.handle.mailbox.stop()
    assert engine.run_turn().status == EngineStatus.STOPPED
    assert not calls


def test_pending_message_is_not_consumed_by_yield_check():
    engine, channel = make_engine(Model(replies=[AIMessage(content="done")]))
    assert engine._tool_yield_reason({}) == "user_message_pending"
    assert engine.conversation.pending_count == 1
    engine.handle.mailbox.stop()
    assert engine._tool_yield_reason({}) == "shutdown_requested"


def test_root_has_no_implicit_send_route():
    inbox = QueueContext()
    inbox.receive("hello")
    inbox.engine.conversation.read_input(wait=False)
    assert inbox.engine.send_message("not a reply") is False
    assert not inbox.channel.sent
