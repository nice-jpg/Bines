from threading import Event, Thread, current_thread
from types import SimpleNamespace

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.types import interrupt

from src.agent_runtime import create_runtime
from src.feishu_agent_runner import FeishuAgentRunner
from test_runtime_communication import Model


class Channel:
    def __init__(self):
        self.sent = []

    def send_text(self, target, text):
        self.sent.append((target.chat_id, text, current_thread().name))


def message(chat, text):
    return SimpleNamespace(chat_id=chat, text=text, target=SimpleNamespace(chat_id=chat))


def drain(runner):
    runner._worker.submit(lambda: None).result(timeout=5)


def test_ingress_during_model_and_other_chat_is_serialized():
    entered, release = Event(), Event()
    model = Model(replies=[AIMessage(content="first"), AIMessage(content="updated"), AIMessage(content="other")])
    def blocking(index):
        if index == 0:
            entered.set()
            assert release.wait(5)
    model.on_call = blocking
    runtime = create_runtime(name="test", model=model, tools=[], system_prompt="base")
    channel = Channel()
    runner = FeishuAgentRunner(runtime, channel, 100)
    try:
        runner.on_message(message("a", "start"))
        assert entered.wait(5)
        runner.on_message(message("a", "supplement"))
        runner.on_message(message("b", "other chat"))
        assert len(model.seen) == 1
        release.set()
        drain(runner)
        assert [(chat, text) for chat, text, _ in channel.sent] == [("a", "updated"), ("b", "other")]
        assert all(name.startswith("agent-worker") for _, _, name in channel.sent)
        assert any(m.content == "supplement" for m in model.seen[1])
        assert not any(m.content == "supplement" for m in model.seen[2])
        assert runtime._communications == {}
    finally:
        release.set()
        runner.close()


def test_supplement_does_not_approve_pending_human_interrupt():
    approvals = []
    @tool
    def approve() -> str:
        """Wait for explicit approval."""
        approvals.append(interrupt({"kind": "human", "message": "approve"}))
        return "approved"
    model = Model(replies=[AIMessage(content="", tool_calls=[{"name": "approve", "args": {}, "id": "approve-1"}]), AIMessage(content="done")])
    runtime = create_runtime(name="test", model=model, tools=[approve], system_prompt="base")
    channel = Channel()
    runner = FeishuAgentRunner(runtime, channel, 100)
    try:
        runner.on_message(message("a", "start"))
        drain(runner)
        runner.on_message(message("a", "clarification"))
        drain(runner)
        assert approvals == []
        runner.on_message(message("a", "done"))
        drain(runner)
        assert approvals == ["done"]
        assert any(m.content == "clarification" for m in model.seen[-1])
    finally:
        runner.close()


def test_close_waits_for_inflight_model_and_prevents_new_model_call():
    entered, release = Event(), Event()
    def block(index):
        entered.set()
        assert release.wait(5)
    model = Model(replies=[AIMessage(content="done")], on_call=block)
    runtime = create_runtime(name="test", model=model, tools=[], system_prompt="base")
    runner = FeishuAgentRunner(runtime, Channel(), 100)
    runner.on_message(message("a", "start"))
    assert entered.wait(5)
    stopped = Event()
    closer = Thread(target=lambda: (runner.close(), stopped.set()))
    closer.start()
    release.set()
    closer.join(5)
    assert stopped.is_set()
    runner.on_message(message("a", "late"))
    assert len(model.seen) == 1
