"""Channel communication stays on the owner loop; model work does not."""

import asyncio
import json
from threading import get_ident

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from test_agent_router import EchoModel

from src.agent_hypervisor.host import Host
from src.agent_hypervisor.root import RootAgent
from src.channel.base import Channel
from src.channel.gateway import Gateway
from src.channel.messages import InBoundMessage, OutBoundMessage


class ChannelStub(Channel):
    def __init__(self, identity="binding"):
        self.identity = identity
        self.sent = []
        self.threads = []
        self.delivery = asyncio.Event()
        self.closed = False

    @property
    def channel_id(self):
        return self.identity

    def bind_buffer(self, buffer):
        self.listener = buffer.submit

    async def start(self):
        self.threads.append(get_ident())

    async def close(self):
        self.threads.append(get_ident())
        self.closed = True

    async def send_message(self, message):
        self.sent.append(message)
        self.threads.append(get_ident())
        self.delivery.set()
        return True


class ReplyModel(EchoModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.threads.append(get_ident())
        if isinstance(messages[-1], ToolMessage):
            reply = AIMessage(content="Not sent implicitly.")
        else:
            handled = {call["id"] for m in messages if isinstance(m, AIMessage) for call in m.tool_calls}
            records = [json.loads(line) for m in messages if m.type == "human" for line in m.content.splitlines()[1:]]
            records = [record for record in records if str(record["sequence"]) not in handled]
            reply = AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": str(record["sequence"]),
                        "name": "reply",
                        "type": "tool_call",
                        "args": {"channel_id": record["channel_id"], "session_id": record["session_id"], "message": json.loads(record["text"])["messages"][0]["text"]},
                    }
                    for record in records
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=reply)])


def test_channel_serves_multiple_conversations_on_one_context():
    async def run():
        channel = ChannelStub()
        model = ReplyModel()
        host = Host()
        engine = RootAgent(model=model, handle=host.register_root())
        await host.open(engine, [channel])
        try:
            for conversation in ("a", "b"):
                channel.delivery.clear()
                assert channel.listener(
                    InBoundMessage("binding", conversation, conversation, conversation, "user")
                )
                await asyncio.wait_for(channel.delivery.wait(), 3)
            assert [(m.session_id, m.text) for m in channel.sent] == [("a", "a"), ("b", "b")]
            assert all(identity != get_ident() for identity in model.threads)
        finally:
            await host.close()
        assert channel.closed and all(identity == get_ident() for identity in channel.threads)
        assert not host._workers

    asyncio.run(run())


def test_delivery_failure_is_returned_to_caller():
    async def run():
        class FailingChannel(ChannelStub):
            async def send_message(self, message):
                return False

        output = Gateway([FailingChannel()])
        output.buffer.submit(InBoundMessage("binding", "a", "1", "hello", "user"))
        batch = await output.take_batch()
        output.commit_batch(batch.batch_id)
        assert await output.send(OutBoundMessage("binding", "a", "hello")) is False

    asyncio.run(run())


def test_topology_rejects_duplicate_channels_before_starting():
    host = Host()
    channel = ChannelStub()
    engine = RootAgent(model=ReplyModel(), handle=host.register_root())
    with pytest.raises(ValueError):
        Gateway([channel, channel])
    engine.close()


def test_start_failure_closes_workers_and_channel():
    async def run():
        class BrokenChannel(ChannelStub):
            async def start(self):
                raise RuntimeError("startup failed")

        channel = BrokenChannel()
        host = Host()
        engine = RootAgent(model=ReplyModel(), handle=host.register_root())
        with pytest.raises(RuntimeError, match="startup failed"):
            await host.open(engine, [channel])
        assert channel.closed and not host._workers

    asyncio.run(run())


def test_root_delegates_and_relays_child_report_to_original_conversation():
    from test_agent_router import EchoEngine

    class DelegatingModel(EchoModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            last = messages[-1]
            if isinstance(last, ToolMessage):
                if last.name == "spawn_subagent":
                    spawned = json.loads(last.content)
                    origin = next(json.loads(m.content.splitlines()[-1]) for m in reversed(messages) if m.type == "human")
                    calls = [
                        {
                            "name": "call",
                            "args": {
                                "subagent_id": spawned["subagent_id"],
                                "task": "delegated task",
                                "source_batch_ids": [origin["batch_id"]],
                            },
                            "id": "call",
                        }
                    ]
                else:
                    calls = []
            else:
                record = json.loads(last.content.splitlines()[-1])
                if record["kind"] == "user":
                    previous_spawn = next(
                        (m for m in messages if isinstance(m, ToolMessage) and m.name == "spawn_subagent"),
                        None,
                    )
                    calls = [
                        {
                            "name": "spawn_subagent",
                            "args": {
                                "agent_type": "echo",
                            },
                            "id": "spawn",
                        }
                    ]
                    if previous_spawn is not None:
                        calls = [
                            {
                                "name": "call",
                                "args": {
                                    "subagent_id": json.loads(previous_spawn.content)["subagent_id"],
                                    "task": "delegated task",
                                    "source_batch_ids": [record["batch_id"]],
                                },
                                "id": "reuse",
                            }
                        ]
                else:
                    # Resolve the report to its originating call, not the latest user input.
                    call_index = next(
                        i
                        for i, m in enumerate(messages)
                        if isinstance(m, ToolMessage)
                        and m.name == "call"
                        and json.loads(m.content)["task_id"] == record["task_id"]
                    )
                    user_records = [
                        json.loads(m.content.splitlines()[-1])
                        for m in messages[:call_index]
                        if m.type == "human"
                    ]
                    origin = next(r for r in reversed(user_records) if r["kind"] == "user")
                    calls = [
                        {
                            "name": "reply",
                            "args": {
                                "channel_id": origin["channel_id"],
                                "session_id": origin["session_id"],
                                "message": record["result"]["summary"],
                            },
                            "id": "reply",
                        }
                    ]
            return ChatResult(
                generations=[
                    ChatGeneration(message=AIMessage(content="" if calls else "Waiting", tool_calls=calls))
                ]
            )

    async def run():
        channel = ChannelStub()

        class DelegatingEngine(RootAgent):
            def spawn_subagent(self, agent_type: str) -> dict[str, str]:
                """Create an echo child."""
                assert agent_type == "echo"
                with self.handle.spawner.child() as handle:
                    self.handle.spawner.start(EchoEngine(model=EchoModel(), handle=handle))
                return {"subagent_id": handle.agent_id}

        host = Host()
        engine = DelegatingEngine(model=DelegatingModel(), handle=host.register_root())
        await host.open(engine, [channel])
        try:
            assert channel.listener(InBoundMessage("binding", "chat-b", "1", "delegate", "user"))
            await asyncio.wait_for(channel.delivery.wait(), 5)
            assert [(message.session_id, message.text) for message in channel.sent] == [
                ("chat-b", "delegated task")
            ]
            assert len(host._workers) == 2
            channel.delivery.clear()
            assert channel.listener(InBoundMessage("binding", "chat-c", "2", "delegate again", "user"))
            await asyncio.wait_for(channel.delivery.wait(), 5)
            assert [(m.session_id, m.text) for m in channel.sent] == [
                ("chat-b", "delegated task"),
                ("chat-c", "delegated task"),
            ]
            assert len(host._workers) == 2
        finally:
            await host.close()

    asyncio.run(run())
