"""Delegation uses persistent workers, not cloned synchronous agent calls."""

import asyncio
from threading import Event

import pytest
from engine_helpers import Model, ScriptedEngine
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool
from test_agent_router import EchoEngine, EchoModel

from src.agent_runtime.contracts import AgentHandle
from src.agent_hypervisor.host import Host
from src.channel.messages import InBoundMessage
from src.agent_runtime.contracts import TaskResult
from src.agent_runtime.types.execution import EngineStatus


def fixture():
    host = Host()
    root = host.router.register_root()
    host.router.deliver_user(InBoundMessage("test", "chat", "1", "task", "user"))
    child = host.router.register_child(root)
    return host, root, child


def test_active_child_does_not_block_parent_business_tools():
    host, root, child = fixture()
    host.router.call(root, child, "work", "work")
    called = []

    def independent_work() -> str:
        """Perform work independent of the child."""
        called.append(True)
        return "done"

    model = Model(
        replies=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "independent_work",
                        "args": {},
                        "id": "work",
                    }
                ],
            ),
            AIMessage(content="done"),
        ]
    )
    engine = ScriptedEngine(
        model=model, handle=host.handle(root), tools=[StructuredTool.from_function(independent_work)]
    )
    try:
        assert engine.run_turn().status == EngineStatus.COMPLETED
        assert called == [True]
    finally:
        engine.close()


def test_active_child_does_not_bypass_parent_result_validation():
    host, root, child = fixture()
    host.router.call(root, child, "work", "work")
    engine = ScriptedEngine(
        model=Model(
            replies=[
                AIMessage(
                    content='{"resolved": false, "summary": "unfinished", "result": {}, "next_action": "continue"}'
                )
            ]
        ),
        handle=host.handle(root),
        structured=True,
    )
    try:
        assert engine.run_turn().status == EngineStatus.CONTINUE_REQUIRED
    finally:
        engine.close()


def test_parent_worker_progresses_while_child_worker_is_busy():
    child_busy, release_child, parent_worked = Event(), Event(), Event()

    def child_work() -> str:
        """Hold independent child work open until the parent has progressed."""
        child_busy.set()
        assert release_child.wait(5)
        return "child work done"

    def parent_work() -> str:
        """Perform work while the child is still busy."""
        assert child_busy.is_set() and not release_child.is_set()
        parent_worked.set()
        return "parent work done"

    class BusyModel(EchoModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            last = messages[-1]
            if last.type == "human":
                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "child_work",
                                        "args": {},
                                        "id": "busy",
                                    }
                                ],
                            )
                        )
                    ]
                )
            if isinstance(last, ToolMessage) and last.name == "child_work":
                assignment = next(m for m in reversed(messages) if m.type == "human")
                return super()._generate([assignment], **kwargs)
            return super()._generate(messages, **kwargs)

    class BusyEngine(EchoEngine):
        def _create_tools(self):
            return [StructuredTool.from_function(child_work)]

    host = Host()
    root = host.register_root()
    parent = ScriptedEngine(
        handle=root,
        model=Model(
            replies=[
                AIMessage(content="", tool_calls=[{"name": "parent_work", "args": {}, "id": "independent"}]),
                AIMessage(content="done"),
            ]
        ),
        tools=[StructuredTool.from_function(parent_work)],
    )
    host.start(parent)
    try:
        with host.child(root.agent_id) as child:
            host.start(BusyEngine(model=BusyModel(), handle=child))
        host.router.call(root.agent_id, child.agent_id, "child task", "child task")
        assert child_busy.wait(3)
        host.router.deliver_user(InBoundMessage("test", "chat", "work", "independent task", "user"))
        assert parent_worked.wait(3)
    finally:
        release_child.set()
        asyncio.run(host.close())


def received(router, agent_id):
    messages = router.read(agent_id, wait=False)
    router.acknowledge(agent_id, tuple(message.message_id for message in messages))
    return messages


def test_blocked_task_yields_and_parent_can_follow_up():
    host, root, child = fixture()
    task = host.router.call(root, child, "work", "work")
    received(host.router, child)
    host.router.report(child, task, TaskResult(status="blocked", summary="Need clarification"))
    assert host.router.tasks_blocked(child)
    followup = host.router.call(root, child, "clarification", "clarification")
    assert received(host.router, child)[0].task_id == followup
    assert not host.router.tasks_blocked(child)
    assert host.router.unfinished(child) == [task, followup]
    host.router.report(child, followup, TaskResult(status="completed", summary="follow-up done"))
    assert host.router.tasks_blocked(child)  # New calls do not silently clear the original blocker.


def test_parent_cannot_bypass_direct_child_relationship():
    host, root, child = fixture()
    grandchild = host.router.register_child(child)
    with pytest.raises(PermissionError):
        host.router.call(root, grandchild, "bypass", "bypass")


def test_stop_reports_cancellation_to_parent():
    host, root, child = fixture()
    task = host.router.call(root, child, "work", "work")
    host.router.read(root, wait=False)
    host.router.stop(child)
    report = host.router.read(root, wait=False)[0]
    assert report.task_id == task and report.result.status == "failed"
    assert not host.router.unfinished(child)


def test_parent_can_report_independent_completion_while_child_is_running():
    from test_agent_router import EchoEngine

    host, root, child = fixture()
    task = host.router.call(root, child, "work", "work")
    received(host.router, child)
    grandchild = host.router.register_child(child)
    host.router.call(child, grandchild, "subtask", "subtask")
    engine = object.__new__(EchoEngine)
    engine.handle = host.handle(child)
    assert engine.report(task, TaskResult(status="completed", summary="own work complete"))
    assert not host.router.tasks_reported(grandchild)


def test_completed_parent_still_processes_later_child_report():
    host, root, child = fixture()
    task = host.router.call(root, child, "own work", "own work")
    received(host.router, child)
    grandchild = host.router.register_child(child)
    subtask = host.router.call(child, grandchild, "background work", "background work")
    received(host.router, grandchild)
    host.router.report(child, task, TaskResult(status="completed", summary="done"))
    host.router.report(grandchild, subtask, TaskResult(status="completed", summary="background result"))
    model = Model(replies=[AIMessage(content="Processed background result")])
    engine = ScriptedEngine(model=model, handle=host.handle(child))
    try:
        assert engine.run_turn().status == EngineStatus.COMPLETED
        assert len(model.seen) == 1
        assert "background result" in model.seen[0][-1].content
    finally:
        engine.close()
