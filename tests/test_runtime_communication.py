import asyncio
from typing import Any
from types import SimpleNamespace

import pytest
from langchain.tools import ToolRuntime
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from src.agent_runtime import create_runtime, RuntimeCommunication
from src.agent_runtime.communication import CommunicationMiddleware, create_talk_tool


class Model(BaseChatModel):
    replies: list[Any]
    seen: list[Any] = []
    on_call: Any = None

    @property
    def _llm_type(self):
        return "communication-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, **kwargs):
        index = len(self.seen)
        self.seen.append(messages)
        if self.on_call:
            self.on_call(index)
        return ChatResult(generations=[ChatGeneration(message=self.replies[min(index, len(self.replies) - 1)])])


def test_proxy_fifo_checkpoint_ack_and_close():
    sent = []
    proxy = RuntimeCommunication(lambda message: sent.append(message) or True)
    assert proxy.talk("warning") is True
    assert sent == ["warning"]
    assert proxy.receive("first") and proxy.receive("second")
    inbox = proxy._root
    batch = inbox.pending([])
    assert [m.content for m in batch] == ["first", "second"]
    assert inbox.pending([]) == batch  # Failure before checkpoint: replay stable IDs.
    assert inbox.pending(batch) == []
    proxy.close()
    assert not proxy.receive("late") and not proxy.talk("late")


def test_sender_failure_is_false():
    def fail(message):
        raise OSError("offline")
    assert RuntimeCommunication(fail).talk("problem") is False
    assert RuntimeCommunication(lambda _: False).talk("problem") is False


def test_session_finish_cannot_discard_accepted_input():
    proxy = RuntimeCommunication(lambda _: True)
    assert proxy.receive("last input")
    assert not proxy._close_if_idle()
    batch = proxy._root.pending([])
    proxy._root.pending(batch)
    assert proxy._close_if_idle()
    assert not proxy.receive("after completion")


def test_terminal_arrival_reenters_same_checkpoint_without_duplicate_user():
    model = Model(replies=[AIMessage(content="first"), AIMessage(content="updated")])
    runtime = create_runtime(name="test", model=model, tools=[], system_prompt="base")
    proxy = runtime.bind_communication("a", lambda _: True)
    model.on_call = lambda index: proxy.receive("new information") if index == 0 else None
    result = runtime.run_turn([HumanMessage(content="start")], session_id="a")
    assert result.output == "updated"
    assert [m.content for m in model.seen[1] if isinstance(m, HumanMessage)] == ["start", "new information"]
    assert not proxy.pending
    assert len([m for m in result.state["messages"] if m.content == "new information"]) == 1


def test_child_and_parent_receive_input_with_independent_histories():
    child_model = Model(replies=[AIMessage(content="child first"), AIMessage(content="child done")])
    child = create_runtime(name="child", model=child_model, tools=[], system_prompt="child")

    def delegate(runtime: ToolRuntime[Any]) -> str:
        """Run the isolated child."""
        state = child.invoke({"messages": [HumanMessage(content="child assignment")]},
                             config={"configurable": {"thread_id": "child-1"}}, context=runtime.context)
        return state["messages"][-1].content

    model = Model(replies=[AIMessage(content="", tool_calls=[{"name": "delegate", "args": {}, "id": "delegate-1"}]), AIMessage(content="parent done")])
    parent = create_runtime(name="parent", model=model, tools=[StructuredTool.from_function(delegate)], system_prompt="parent")
    sent = []
    proxy = parent.bind_communication("parent-1", lambda text: sent.append(text) or True)
    child_model.on_call = lambda index: proxy.receive("fix the baseline") if index == 0 else None
    result = parent.run_turn([HumanMessage(content="start")], session_id="parent-1")
    assert result.output == "parent done"
    assert any(m.content == "fix the baseline" for m in child_model.seen[-1])
    assert any(m.content == "fix the baseline" for m in model.seen[-1])
    assert not any(m.content == "child assignment" for m in model.seen[-1])
    assert list(proxy._inboxes) == ["root"]


def test_talk_tool_hidden_runtime_and_no_interrupt():
    tool = create_talk_tool()
    assert set(tool.tool_call_schema.model_json_schema()["properties"]) == {"message"}
    model = Model(replies=[AIMessage(content="", tool_calls=[{"name": "talk", "args": {"message": "problem"}, "id": "talk-1"}]), AIMessage(content="done")])
    runtime = create_runtime(name="test", model=model, tools=[], system_prompt="base")
    sent = []
    runtime.bind_communication("a", lambda text: sent.append(text) or True)
    assert not runtime.run_turn([HumanMessage(content="start")], session_id="a").interrupted
    assert sent == ["problem"]


def test_async_injection_matches_sync_and_sessions_are_isolated():
    first = RuntimeCommunication(lambda _: True)
    second = RuntimeCommunication(lambda _: True)
    first.receive("only first")
    middleware = CommunicationMiddleware()
    state = {"messages": []}
    context = SimpleNamespace(context=first._root)
    assert asyncio.run(middleware.abefore_model(state, context)) == middleware.before_model(state, context)
    assert middleware.before_model(state, SimpleNamespace(context=second._root)) is None


def test_input_during_tool_preserves_protocol_order():
    def operation() -> str:
        """Perform one operation."""
        proxy.receive("new direction")
        return "operation completed"

    model = Model(replies=[AIMessage(content="", tool_calls=[
        {"name": "operation", "args": {}, "id": "op-1"}
    ]), AIMessage(content="done")])
    runtime = create_runtime(name="test", model=model,
                             tools=[StructuredTool.from_function(operation)], system_prompt="base")
    proxy = runtime.bind_communication("a", lambda _: True)
    result = runtime.run_turn([HumanMessage(content="start")], session_id="a")
    messages = result.state["messages"]
    response_index = next(i for i, m in enumerate(messages) if isinstance(m, ToolMessage))
    user_index = next(i for i, m in enumerate(messages) if m.content == "new direction")
    assert response_index < user_index
    assert not proxy.pending


def test_failed_model_resume_does_not_duplicate_injected_message():
    model = Model(replies=[AIMessage(content="done")])
    runtime = create_runtime(name="test", model=model, tools=[], system_prompt="base")
    proxy = runtime.bind_communication("a", lambda _: True)
    proxy.receive("supplement")
    def fail_first(index):
        if index == 0:
            raise ValueError("model configuration failed")
    model.on_call = fail_first
    assert runtime.run_turn([HumanMessage(content="start")], session_id="a").interrupted
    result = runtime.resume_turn(session_id="a")
    assert not result.interrupted
    assert sum(m.content == "supplement" for m in result.state["messages"]) == 1
    assert not proxy.pending


def test_shutdown_after_generation_does_not_dispatch_new_tool():
    calls = []
    def operation() -> str:
        """Perform one operation."""
        calls.append(True)
        return "done"
    model = Model(replies=[AIMessage(content="", tool_calls=[
        {"name": "operation", "args": {}, "id": "op-1"}
    ])])
    runtime = create_runtime(name="test", model=model,
                             tools=[StructuredTool.from_function(operation)], system_prompt="base")
    runtime.bind_communication("a", lambda _: True)
    model.on_call = lambda _: runtime.close()
    result = runtime.run_turn([HumanMessage(content="start")], session_id="a")
    assert result.stopped_by == "shutdown_interrupt"
    assert calls == []


def test_unbound_talk_returns_false_without_interrupt():
    model = Model(replies=[AIMessage(content="", tool_calls=[
        {"name": "talk", "args": {"message": "problem"}, "id": "talk-1"}
    ]), AIMessage(content="done")])
    runtime = create_runtime(name="test", model=model, tools=[], system_prompt="base")
    result = runtime.run_turn([HumanMessage(content="start")], session_id="a")
    assert not result.interrupted
    response = next(m for m in result.state["messages"] if isinstance(m, ToolMessage))
    assert response.content == "false"


def test_unbound_long_tool_can_observe_runtime_shutdown():
    from src.agent_runtime import tool_yield_reason
    reasons = []
    def operation(runtime: ToolRuntime[Any]) -> str:
        """Finish a finite action and check the cooperative stop signal."""
        agent.close()
        reasons.append(tool_yield_reason(runtime))
        return "yielded"
    model = Model(replies=[AIMessage(content="", tool_calls=[
        {"name": "operation", "args": {}, "id": "op-1"}
    ])])
    agent = create_runtime(name="test", model=model,
                           tools=[StructuredTool.from_function(operation)], system_prompt="base")
    result = agent.run_turn([HumanMessage(content="start")])
    assert reasons == ["shutdown_requested"]
    assert result.stopped_by == "shutdown_interrupt"
    assert not agent._active_channels
    assert not agent._communications
