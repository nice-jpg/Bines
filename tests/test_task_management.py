import json

import pytest

from src.agent_hypervisor.host import Host
from src.agent_hypervisor.routing import MailboxRouter
from src.agent_runtime.contracts import TaskResult
from src.agent_runtime.conversation.session import ConversationSession
from src.channel.messages import ChannelBatch, InBoundMessage, SessionKey


def test_task_pair_requires_parent_acceptance_and_supports_revision() -> None:
    router = MailboxRouter()
    parent = router.register_root()
    child = router.register_child(parent)
    sender, receiver = router.mailbox(parent), router.mailbox(child)
    topic = sender.call(child, "商品脚本验证", "Validate the products")
    receiver.acknowledge(receiver.read(wait=False))
    assert topic in router._agents[parent].tasks.sent
    assert topic in router._agents[child].tasks.received
    assert receiver.report(topic, TaskResult(status="progress", summary="testing"))
    sender.acknowledge(sender.read(wait=False))
    assert router._agents[parent].tasks.sent[topic].status == "progress"
    with pytest.raises(ValueError, match="final report"):
        sender.accept_report(topic)
    receiver.report(topic, TaskResult(status="completed", summary="ready"))
    assert topic not in router._agents[child].tasks.received
    # A queued report is not yet a received or accepted report.
    with pytest.raises(ValueError, match="final report"):
        sender.accept_report(topic)
    sender.acknowledge(sender.read(wait=False))
    assert router._agents[parent].tasks.sent[topic].status == "awaiting_review"
    first_correlation = router._agents[parent].tasks.sent[topic].correlation_id
    assert sender.call(child, topic, "Price extraction is wrong; revise it") == topic
    receiver.acknowledge(receiver.read(wait=False))
    assert router._agents[parent].tasks.sent[topic].correlation_id != first_correlation
    assert router._agents[child].tasks.received[topic].instruction.startswith("Price")
    receiver.report(topic, TaskResult(status="completed", summary="fixed"))
    sender.acknowledge(sender.read(wait=False))
    assert sender.accept_report(topic)
    assert not router._agents[parent].tasks.sent
    assert not router._agents[child].tasks.received
    assert not router._tasks


def test_same_topics_are_scoped_to_each_agent_and_direction() -> None:
    router = MailboxRouter()
    root = router.register_root()
    parent = router.register_child(root)
    child = router.register_child(parent)
    router.call(root, parent, "检查", "Check the page")
    router.mailbox(parent).acknowledge(router.read(parent, wait=False))
    router.call(parent, child, "检查", "Check the script")
    router.mailbox(child).acknowledge(router.read(child, wait=False))
    manager = router._agents[parent].tasks
    assert set(manager.received) == set(manager.sent) == {"检查"}
    assert manager.received["检查"].correlation_id != manager.sent["检查"].correlation_id
    router.report(child, "检查", TaskResult(status="completed", summary="script ready"))
    router.mailbox(parent).acknowledge(router.read(parent, wait=False))
    router.accept_report(parent, "检查")
    assert "检查" in manager.received and not manager.sent


def test_topic_collision_cannot_retarget_an_active_task() -> None:
    router = MailboxRouter()
    parent = router.register_root()
    first, second = router.register_child(parent), router.register_child(parent)
    router.call(parent, first, "检查", "first")
    with pytest.raises(ValueError, match="different child"):
        router.call(parent, second, "检查", "second")
    assert not router.read(second, wait=False)
    for topic in ("", " \n", "x" * 65):
        with pytest.raises(ValueError, match="short"):
            router.call(parent, second, topic, "task")


def test_subagent_query_is_parent_scoped_and_retains_idle_children() -> None:
    router = MailboxRouter()
    parent = router.register_root()
    child = router.register_child(parent)
    grandchild = router.register_child(child)
    router.set_agent_type(child, "collector")
    router.set_agent_type(grandchild, "general")
    sender, receiver = router.mailbox(parent), router.mailbox(child)
    assert sender.list_subagents() == [{"subagent_id": child, "agent_type": "collector", "tasks": []}]
    sender.call(child, "验证", "Verify")
    assert sender.awaiting_reports()
    receiver.acknowledge(receiver.read(wait=False))
    receiver.report("验证", TaskResult(status="completed", summary="ready"))
    sender.acknowledge(sender.read(wait=False))
    assert not sender.awaiting_reports()
    assert sender.list_subagents()[0]["tasks"] == [{"task_id": "验证", "status": "awaiting_review"}]
    sender.accept_report("验证")
    assert sender.list_subagents()[0]["tasks"] == []
    assert not sender.awaiting_reports()
    router.stop(child)
    assert sender.list_subagents() == []


def test_failed_report_delivery_does_not_delete_received_task(monkeypatch: pytest.MonkeyPatch) -> None:
    router = MailboxRouter()
    parent = router.register_root()
    child = router.register_child(parent)
    router.call(parent, child, "工作", "work")
    router.acknowledge(child, tuple(m.message_id for m in router.read(child, wait=False)))
    monkeypatch.setattr(router, "_put", lambda mailbox, message: False)
    assert not router.report(child, "工作", TaskResult(status="completed", summary="done"))
    assert router.unfinished(child) == ["工作"]


def test_model_input_contains_no_transport_metadata() -> None:
    host = Host()
    parent = host.register_root()
    child_id = host.router.register_child(parent.agent_id)
    child = host.handle(child_id)
    session = ConversationSession(child)
    parent.mailbox.call(child_id, "验证商品", "Use structural evidence", {"channel_id": "secret-channel"})
    block = session.read_input(wait=False)
    assert block is not None
    record = json.loads(block.text.splitlines()[1])
    assert record == {"task_id": "验证商品", "text": "Use structural evidence"}
    for internal in (parent.agent_id, child_id, "secret-channel", "sequence", "correlation_id", "origin"):
        assert internal not in block.text


def test_root_external_inputs_have_empty_topic_and_do_not_mix_addresses() -> None:
    host = Host()
    root = host.register_root()
    session = ConversationSession(root)
    for address in ("first", "second"):
        message = InBoundMessage("test", address, address, address, "sender")
        host.router.deliver_batch(root.agent_id, ChannelBatch(address, SessionKey("test", address), 1, (message,)))
    first = session.read_input(wait=False)
    assert first is not None
    assert json.loads(first.text.splitlines()[1]) == {"task_id": "", "text": "first"}
    session.acknowledge(first.block_id)
    assert session.read_input(wait=False) is None
    assert session.current_origin["session_id"] == "first"
    session.finish_turn()
    second = session.read_input(wait=False)
    assert second is not None
    assert json.loads(second.text.splitlines()[1]) == {"task_id": "", "text": "second"}
    assert session.current_origin["session_id"] == "second"
