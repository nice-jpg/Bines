import asyncio
import json
from threading import Thread, get_ident

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from src.agent_runtime.contracts import AgentHandle
from src.agent_hypervisor.host import Host
from src.agent_runtime.conversation import ConversationSession
from src.agent_hypervisor.root import RootAgent
from src.agent_runtime.engine import AgentEngine
from src.channel.messages import InBoundMessage
from src.agent_runtime.contracts import MessageStreamClosed
from src.agent_hypervisor.routing import MailboxRouter, TaskResult


def setup_router():
    router = MailboxRouter()
    root = router.register_root()
    for conversation in ("a", "b"):
        assert router.deliver_user(InBoundMessage("terminal", conversation, conversation, "hello", "user"))
    return router, root


def test_channel_is_not_a_conversation():
    router, root = setup_router()
    messages = router.read(root, wait=False)
    assert [message.session_id for message in messages] == ["a", "b"]
    assert messages[0].sequence < messages[1].sequence
    with pytest.raises(ValueError):
        router.register_root()


def test_parent_child_permissions_and_task_correlation():
    router, root = setup_router()
    a = router.register_child(root)
    b = router.register_child(root)
    task = router.call(root, a, "work", "work")
    completed = TaskResult(status="completed", summary="done")
    with pytest.raises(PermissionError):
        router.report(a, task, completed)
    received = router.read(a, wait=False)
    assert received[0].task_id == task
    router.acknowledge(a, tuple(message.message_id for message in received))
    with pytest.raises(PermissionError):
        router.call(a, b, "wrong", "wrong")
    with pytest.raises(PermissionError):
        router.report(b, task, completed)
    assert router.report(a, task, completed)
    report = router.read(root, wait=False)[-1]
    assert report.task_id == task and report.session_id is None
    assert router.report(a, task, completed)  # Retry of identical terminal report is idempotent.
    with pytest.raises(ValueError):
        router.report(a, task, TaskResult(status="failed", summary="contradictory"))
    assert not router.unfinished(a)


def test_kill_wakes_blocked_reader():
    router, root = setup_router()
    child = router.register_child(root)
    stopped = []

    def read():
        with pytest.raises(MessageStreamClosed):
            router.read(child, wait=True)
        stopped.append(True)

    thread = Thread(target=read)
    thread.start()
    router.stop(child)
    thread.join(timeout=2)
    assert stopped


def test_session_preserves_conversation_lists_and_blocks():
    host = Host()
    root = host.router.register_root()
    session = ConversationSession(host.handle(root))
    for index, conversation in enumerate(("a", "b", "a")):
        host.router.deliver_user(InBoundMessage("terminal", conversation, str(index), str(index), "user"))
    block = session.read_input(wait=False)
    assert block.source_texts == ("0", "1", "2")
    assert [message.text for message in session.conversations[("terminal", "a")]] == ["0", "2"]
    assert session.read_input(wait=False) is block
    session.acknowledge(block.block_id)
    assert session.read_input(wait=False) is None
    session.close()


def test_root_graph_tool_schema():
    host = Host()
    root = host.router.register_root()
    engine = RootAgent(
        model=GenericFakeChatModel(messages=iter([AIMessage(content="waiting")])),
        handle=host.handle(root),
    )
    assert {tool.name for tool in engine._create_tools()} == {"reply"}
    from src.agent_runtime.messaging.tools import communication_tools

    spawn = next(tool for tool in communication_tools(engine) if tool.name == "spawn_subagent")
    assert set(spawn.args) == {"agent_type"}
    engine.close()


def test_failed_child_construction_releases_identity():
    host = Host()
    parent = host.register_root()
    with pytest.raises(RuntimeError, match="construction failed"):
        with host.child(parent.agent_id):
            raise RuntimeError("construction failed")
    assert host.router.children(parent.agent_id) == []
    with pytest.raises(RuntimeError, match="host.start"):
        with host.child(parent.agent_id):
            pass
    assert host.router.children(parent.agent_id) == []
    asyncio.run(host.close())


def test_unstarted_child_can_be_killed_during_construction():
    host = Host()
    parent = host.register_root()
    with pytest.raises(ValueError, match="not owned"):
        with host.child(parent.agent_id) as handle:
            engine = EchoEngine(model=EchoModel(), handle=handle)
            host.kill(parent.agent_id, handle.agent_id)
            host.start(engine)
    assert host.router.children(parent.agent_id) == []
    asyncio.run(host.close())


class EchoModel(BaseChatModel):
    threads: list[int] = []

    @property
    def _llm_type(self):
        return "worker-test"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.threads.append(get_ident())
        if isinstance(messages[-1], ToolMessage):
            reply = AIMessage(content="Waiting for another task.")
        else:
            envelope = json.loads(messages[-1].content.splitlines()[-1])
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": envelope["task_id"],
                        "name": "report",
                        "type": "tool_call",
                        "args": {
                            "task_id": envelope["task_id"],
                            "result": {
                                "status": "completed",
                                "summary": envelope["text"],
                                "data": {},
                            },
                        },
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])


class EchoEngine(AgentEngine):
    def _name(self):
        return "echo"

    def _system_prompt(self):
        return "Report every task."

    def _create_tools(self):
        return []


def test_child_owns_thread_and_survives_two_completed_tasks():
    host = Host()
    parent = host.router.register_root()
    host.router.deliver_user(InBoundMessage("test", "chat", "1", "start", "user"))
    host.router.read(parent, wait=False)
    model = EchoModel()
    with host.child(parent) as handle:
        host.start(EchoEngine(model=model, handle=handle))
    child = handle.agent_id
    try:
        for text in ("first", "second"):
            host.router.deliver_user(InBoundMessage("test", text, text, text, "user"))
            assert host.router.read(parent, wait=False)[0].session_id == text
            task = host.router.call(parent, child, text, text)
            report = host.router.read(parent, wait=True)[0]
            assert report.task_id == task
            assert report.result.status == "completed"
            assert report.result.summary == text
            assert report.session_id is None
        assert model.threads and all(identity != get_ident() for identity in model.threads)
        assert host.router.children(parent) == [child]
        host.kill(parent, child)
        assert host.router.children(parent) == []
    finally:
        asyncio.run(host.close())
        host.router.release(parent)
