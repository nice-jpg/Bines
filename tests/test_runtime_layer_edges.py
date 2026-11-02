import asyncio

import pytest
from engine_helpers import Model, ScriptedEngine
from langchain_core.messages import AIMessage, HumanMessage
from test_agent_router import EchoModel
from test_channel_workers import ChannelStub, ReplyModel

from src.agent_hypervisor.host import Host
from src.agent_hypervisor.root import RootAgent
from src.agent_runtime.types.execution import EngineStatus
from src.channel.gateway import Gateway
from src.channel.messages import InBoundMessage


def test_same_session_in_two_channels_uses_one_root_and_correct_outputs() -> None:
    async def run() -> None:
        channels = [ChannelStub("a"), ChannelStub("b")]
        host = Host()
        root = RootAgent(model=ReplyModel(), handle=host.register_root())
        await host.open(root, channels)
        try:
            for channel in channels:
                channel.listener(InBoundMessage(channel.channel_id, "same", "1", channel.channel_id, "user"))
            await asyncio.wait_for(asyncio.gather(*(c.delivery.wait() for c in channels)), 3)
            assert [channel.sent[0].text for channel in channels] == ["a", "b"]
            assert len(host._workers) == 1
            assert set(root.conversation.conversations) == {("a", "same"), ("b", "same")}
        finally:
            await host.close()

    asyncio.run(run())


def test_root_validates_batch_origins_and_reports_preserve_address() -> None:
    async def run() -> None:
        host = Host()
        root = RootAgent(model=EchoModel(), handle=host.register_root())
        gateway = Gateway([ChannelStub("test")])
        root.bind_gateway(gateway)
        child_id = host.router.register_child(root.handle.agent_id)
        child = host.router.mailbox(child_id)
        for session in ("first", "latest"):
            gateway.buffer.submit(InBoundMessage("test", session, session, session, "user"))
            batch = await gateway.take_batch()
            host.router.deliver_batch(root.handle.agent_id, batch)
            gateway.commit_batch(batch.batch_id)
            root.conversation.finish_turn()
            block = root.conversation.read_input(wait=False)
            if session == "first":
                task_id = root.call(child_id, "work", "work")["task_id"]
            root.conversation.acknowledge(block.block_id)
        with pytest.raises(ValueError):
            root.call(child_id, "", "bad")
        mail = child.read(wait=False)
        child.acknowledge(mail)
        from src.agent_runtime.contracts import TaskResult

        child.report(task_id, TaskResult(status="completed", summary="done"))
        report = host.router.read(root.handle.agent_id, wait=False)[0]
        assert report.origin["session_id"] == "first"
        assert root.handle.mailbox.task_origin(task_id)["session_id"] == "first"
        root.close()
        host.router.stop(child_id)
        host.router.release(child_id)
        await host.close()

    asyncio.run(run())


def test_async_nested_engine_does_not_inherit_parent_checkpoint_namespace() -> None:
    from langchain_core.runnables.config import set_config_context

    async def run() -> None:
        engine = ScriptedEngine(
            model=Model(replies=[AIMessage(content="done")]), initial_messages=[HumanMessage(content="task")]
        )
        try:
            with set_config_context(
                {
                    "configurable": {
                        "thread_id": "parent",
                        "checkpoint_ns": "parent:tool",
                        "checkpoint_id": "not-a-child-checkpoint",
                    }
                }
            ) as context:
                result = await asyncio.create_task(engine.arun_turn(), context=context)
            assert result.status == EngineStatus.COMPLETED
            snapshot = engine.agent.get_state({"configurable": {"thread_id": engine._checkpoint_id}})
            assert snapshot.values["messages"][-1].content == "done"
            assert snapshot.config["configurable"].get("checkpoint_ns", "") == ""
        finally:
            engine.close()

    asyncio.run(run())
