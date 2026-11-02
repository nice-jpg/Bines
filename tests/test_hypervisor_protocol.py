import ast
import asyncio
from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from test_agent_router import EchoModel
from test_channel_workers import ChannelStub

from src.agent_hypervisor.batch import BatchCoordinator
from src.agent_hypervisor.host import Host
from src.agent_hypervisor.root import RootAgent
from src.agent_hypervisor.routing import AuthenticatedMailboxPort, MailboxRouter
from src.agent_runtime.contracts import MailboxToken, MessageStreamClosed, TaskResult
from src.channel.buffer import BufferClosed, MessageBuffer
from src.channel.gateway import Gateway
from src.channel.messages import InBoundMessage


def message(session: str, identity: str, channel: str = "test") -> InBoundMessage:
    return InBoundMessage(channel, session, identity, identity, "user")


def test_gateway_orders_groups_by_first_arrival_and_freezes_claims() -> None:
    async def run() -> None:
        buffer = MessageBuffer()
        for item in (message("a", "a1"), message("b", "b1"), message("a", "a2")):
            assert buffer.submit(item)
        first = await buffer.claim()
        assert [m.text for m in first.messages] == ["a1", "a2"]
        assert buffer.submit(message("a", "a3"))
        second = await buffer.claim()
        assert [m.text for m in second.messages] == ["b1"]
        buffer.release(first.batch_id)
        assert await buffer.claim() == first
        buffer.commit(first.batch_id)
        buffer.commit(second.batch_id)
        third = await buffer.claim()
        assert [m.text for m in third.messages] == ["a3"]
        buffer.close()
        with pytest.raises(BufferClosed):
            await buffer.claim()

    asyncio.run(run())


def test_buffer_deduplicates_by_channel_and_session() -> None:
    async def run() -> None:
        buffer = MessageBuffer()
        assert buffer.submit(message("same", "1", "channel-a"))
        assert buffer.submit(message("same", "1", "channel-a"))
        assert buffer.submit(message("same", "1", "channel-b"))
        assert len((await buffer.claim()).messages) == 1
        assert (await buffer.claim()).address.channel_id == "channel-b"

    asyncio.run(run())


def test_batch_transfer_is_idempotent_and_wakes_root() -> None:
    async def run() -> None:
        host = Host()
        root = host.register_root()
        gateway = Gateway([ChannelStub("test")])
        gateway.buffer.submit(message("chat", "1"))
        batch = await gateway.take_batch()
        assert host.router.deliver_batch(root.agent_id, batch)
        assert host.router.deliver_batch(root.agent_id, batch)
        mails = await root.mailbox.receive()
        assert len(mails) == 1 and mails[0].batch_id == batch.batch_id
        root.mailbox.acknowledge(mails)
        assert root.mailbox.read(wait=False) == []
        gateway.release_batch(batch.batch_id)
        coordinator = BatchCoordinator(gateway, host.router, root.agent_id)
        task = asyncio.create_task(coordinator.run())
        await asyncio.sleep(0)
        gateway.buffer.close()
        await task
        assert root.mailbox.read(wait=False) == []
        await host.close()

    asyncio.run(run())


def test_agent_id_is_not_a_mailbox_credential() -> None:
    router = MailboxRouter()
    root = router.register_root()
    child = router.register_child(root)
    port = AuthenticatedMailboxPort(router)
    with pytest.raises(PermissionError):
        port.call(MailboxToken(root), child, "forged", "forged", {})
    mailbox = router.mailbox(root)
    assert mailbox.call(child, "authorized", "authorized")
    router.stop(root)
    with pytest.raises(MessageStreamClosed):
        mailbox.call(child, "revoked", "revoked")


def test_retransmission_keeps_task_identity_and_requires_explicit_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr("src.agent_hypervisor.routing.monotonic", lambda: clock[0])
    router = MailboxRouter()
    parent = router.register_root()
    child = router.register_child(parent)
    mailbox = router.mailbox(child)
    task_id = router.mailbox(parent).call(child, "work", "work")
    original = mailbox.read(wait=False)
    assert not mailbox.unfinished()
    clock[0] = 31
    router.retry_expired()
    retry = mailbox.read(wait=False)
    assert retry == original
    assert retry[0].task_id == task_id
    mailbox.acknowledge(original)
    mailbox.acknowledge(retry)
    assert mailbox.unfinished() == [task_id]
    clock[0] = 1000
    router.retry_expired()
    assert mailbox.read(wait=False) == []
    assert mailbox.report(task_id, TaskResult(status="progress", summary="step one"))
    completed = TaskResult(status="completed", summary="done")
    assert mailbox.report(task_id, completed)
    assert mailbox.report(task_id, completed)
    reports = router.read(parent, wait=False)
    assert len(reports) == 2


def test_unacknowledged_task_reports_unknown_not_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [0.0]
    monkeypatch.setattr("src.agent_hypervisor.routing.monotonic", lambda: clock[0])
    router = MailboxRouter()
    parent = router.register_root()
    child = router.register_child(parent)
    task_id = router.call(parent, child, "work", "work")
    for _ in range(4):
        clock[0] += 31
        router.retry_expired()
    notices = router.read(parent, wait=False)
    assert len(notices) == 1 and notices[0].kind == "notice"
    assert notices[0].task_id == task_id and notices[0].result is None
    mails = router.mailbox(child).read(wait=False)
    assert len(mails) == 1
    router.acknowledge(child, (mails[0].message_id,))
    assert router.report(child, task_id, TaskResult(status="completed", summary="late success"))


def test_async_receive_cancellation_and_close_leave_no_waiters() -> None:
    async def run() -> None:
        router = MailboxRouter()
        root = router.register_root()
        mailbox = router.mailbox(root)
        pending = asyncio.create_task(mailbox.receive())
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not router._agents[root].waiters
        pending = asyncio.create_task(mailbox.receive())
        await asyncio.sleep(0)
        router.stop(root)
        with pytest.raises(MessageStreamClosed):
            await pending

    asyncio.run(run())


def test_general_fork_owns_graph_checkpoint_tools_and_mailbox() -> None:
    async def run() -> None:
        host = Host()
        root = RootAgent(model=EchoModel(), handle=host.register_root())
        root._fork_context.before_model({"messages": [HumanMessage(content="context", id="original")]}, None)
        child_id = root.spawn_subagent("general")["subagent_id"]
        child = host._workers[child_id]._coordinator._engine
        try:
            assert type(child) is type(root)
            assert child.agent is not root.agent
            assert child._checkpoint_id != root._checkpoint_id
            assert child.handle.mailbox is not root.handle.mailbox
            root._fork_context._state["messages"][0].content = "changed"
            assert child._state["messages"][0].content == "context"
            with pytest.raises(PermissionError):
                child.reply("", "must not escape")
        finally:
            await host.close()
            root.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "directory,forbidden",
    [
        ("channel", ("src.agent_runtime", "src.agent_hypervisor")),
        (
            "agent_runtime",
            ("src.channel", "src.agent_hypervisor", "src.application", "src.collector", "src.conductor"),
        ),
        ("agent_hypervisor", ("src.collector", "src.conductor")),
    ],
)
def test_layer_import_boundaries(directory: str, forbidden: tuple[str, ...]) -> None:
    for file in (Path(__file__).parents[1] / "src" / directory).rglob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            modules = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            assert not any(name.startswith(forbidden) for name in modules), file
