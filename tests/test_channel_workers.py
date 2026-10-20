"""Integration coverage for the replacement channel-independent workers."""

import json
from unittest.mock import create_autospec

import pytest
from queue import Queue
from threading import Event, current_thread
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.types import interrupt

from src.agent_runtime.coordinator import AgentCoordinator
from src.agent_runtime.worker import AgentWorker
from src.agent_runtime.types.execution import EngineStatus
from engine_helpers import ScriptedEngine
from src.channel.base import Channel
from src.agent_runtime.messaging.messages import IncomingMessage, MessageStreamClosed
from engine_helpers import Model


from gateway_helpers import RecordingChannel, GatewayHandle


def completed(summary):
    return AIMessage(
        content=json.dumps(
            {
                "resolved": True,
                "summary": summary,
                "result": {},
                "next_action": None,
            }
        )
    )


def workers(settings, channels):
    assert len(channels) == 1
    gateway = GatewayHandle(next(iter(channels.values())))
    engine = ScriptedEngine(**settings, gateway_id=gateway.gateway_id, structured=True)
    worker = AgentWorker(AgentCoordinator(engine))
    worker.start()
    gateway.start()
    return worker, gateway


def stop(worker, gateway):
    gateway.request_stop()
    worker.join()
    gateway.finish_output()
    gateway.join()


def test_long_running_agent_completes_unresolved_work_then_accepts_next_task():
    unresolved = AIMessage(
        content=json.dumps(
            {
                "resolved": False,
                "summary": "working",
                "result": {},
                "next_action": "finish task",
            }
        )
    )
    model = Model(replies=[unresolved, completed("finished"), completed("next finished")])
    settings = dict(name="test", model=model, tools=[], system_prompt="base")
    channel = RecordingChannel()
    agent, gateway = workers(settings, {"feishu": channel})
    try:
        assert channel.receive("chat", "first task")
        assert json.loads(channel.output().text)["summary"] == "finished"
        assert len(model.seen) == 2
        assert channel.receive("chat", "second task")
        assert json.loads(channel.output().text)["summary"] == "next finished"
        assert len(model.seen) == 3
        assert channel.sent.empty()
    finally:
        stop(agent, gateway)


def test_supplement_does_not_approve_pending_human_interrupt():
    approvals = []

    @tool
    def approve() -> str:
        """Wait for explicit approval."""
        approvals.append(interrupt({"kind": "human", "message": "approve"}))
        return "approved"

    model = Model(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "approve", "args": {}, "id": "approve-1"}]),
            completed("done"),
        ]
    )
    settings = dict(name="test", model=model, tools=[approve], system_prompt="base")
    channel = RecordingChannel()
    worker, gateway = workers(settings, {"feishu": channel})
    try:
        channel.receive("a", "start")
        assert channel.output().text == "approve"
        channel.receive("a", "clarification")
        assert approvals == []
        channel.receive("a", "done")
        assert json.loads(channel.output().text)["summary"] == "done"
        assert approvals == ["done"]
        assert any("user: clarification" in m.content for m in model.seen[-1])
    finally:
        stop(worker, gateway)


def test_shutdown_waits_for_inflight_call_and_prevents_new_tool():
    entered, release = Event(), Event()
    calls = []

    @tool
    def operation() -> str:
        """Record an operation."""
        calls.append(True)
        return "ok"

    def block(index):
        entered.set()
        assert release.wait(5)

    model = Model(
        replies=[AIMessage(content="", tool_calls=[{"name": "operation", "args": {}, "id": "one"}])],
        on_call=block,
    )
    settings = dict(name="test", model=model, tools=[operation], system_prompt="base")
    channel = RecordingChannel()
    worker, gateway = workers(settings, {"feishu": channel})
    try:
        channel.receive("a", "start")
        assert entered.wait(5)
        gateway.request_stop()
        release.set()
        assert worker.join().status == EngineStatus.STOPPED
        worker.join()
        gateway.finish_output()
        gateway.join()
        assert not calls
        assert len(model.seen) == 1
    finally:
        release.set()


def test_notifications_deliver_while_agent_tool_is_still_running():
    from langchain.tools import ToolRuntime

    entered, release = Event(), Event()

    @tool
    def notify_and_wait(runtime: ToolRuntime[None]) -> str:
        """Emit a notice before completing a long operation."""
        assert worker._coordinator._engine.send_message("notice")
        entered.set()
        assert release.wait(5)
        return "tool done"

    model = Model(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "notify_and_wait",
                        "args": {},
                        "id": "notice-1",
                    }
                ],
            ),
            completed("final"),
        ]
    )
    settings = dict(name="test", model=model, tools=[notify_and_wait], system_prompt="base")
    channel = RecordingChannel()
    worker, gateway = workers(settings, {"feishu": channel})
    try:
        channel.receive("chat", "start")
        assert entered.wait(5)
        assert channel.output().text == "notice"
        assert len(model.seen) == 1
        release.set()
        assert json.loads(channel.output().text)["summary"] == "final"
    finally:
        release.set()
        stop(worker, gateway)


def test_conversation_address_stays_fixed_while_supplements_arrive():
    entered, release = Event(), Event()

    def block(index):
        if index == 0:
            entered.set()
            assert release.wait(5)

    model = Model(replies=[completed("first"), completed("updated"), completed("next")], on_call=block)
    settings = dict(name="test", model=model, tools=[], system_prompt="base")
    channel = RecordingChannel()
    worker, gateway = workers(settings, {"feishu": channel})
    try:
        channel.receive("group", "start")
        assert entered.wait(5)
        channel.receive("group", "supplement")
        release.set()
        assert channel.output().conversation_id == "group"
        channel.receive("group", "next task")
        assert channel.output().conversation_id == "group"
    finally:
        release.set()
        stop(worker, gateway)


def test_input_arriving_during_interrupt_is_reported_without_approving():
    entered, release = Event(), Event()
    approvals = []

    @tool
    def approve() -> str:
        """Wait for an explicit human confirmation."""
        entered.set()
        assert release.wait(5)
        approvals.append(interrupt({"kind": "human", "message": "confirm"}))
        return "approved"

    model = Model(
        replies=[
            AIMessage(content="", tool_calls=[{"name": "approve", "args": {}, "id": "one"}]),
            completed("final"),
        ]
    )
    settings = dict(name="test", model=model, tools=[approve], system_prompt="base")
    channel = RecordingChannel()
    worker, gateway = workers(settings, {"feishu": channel})
    try:
        channel.receive("chat", "start")
        assert entered.wait(5)
        channel.receive("chat", "supplement while interrupting")
        release.set()
        assert channel.output().text == "confirm"
        assert approvals == []
        channel.receive("chat", "done")
        assert json.loads(channel.output().text)["summary"] == "final"
        assert approvals == ["done"]
        assert any("user: supplement while interrupting" in m.content for m in model.seen[-1])
    finally:
        release.set()
        stop(worker, gateway)


