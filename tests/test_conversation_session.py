"""Single-conversation boundaries and lossless, stable dialogue blocks."""
from threading import Event, Thread
import json

import pytest
from gateway_helpers import GatewayHandle, RecordingChannel
from src.agent_runtime.conversation import ConversationSession
from src.agent_runtime.contracts import MessageStreamClosed


def session_pair():
    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    return ConversationSession(gateway.handle), channel, gateway


def test_all_backlog_forms_one_block_with_speakers_mentions_and_multiline_boundaries():
    session, channel, gateway = session_pair()
    channel.receive("chat", "@robot 检查xx目录\nB: 这仍是A的正文", sender="a", name="A")
    channel.receive("chat", "已经找到了", sender="b", name="B")
    block = session.read_input(wait=False)
    records = [json.loads(line) for line in block.text.splitlines()[1:]]
    assert [record["text"] for record in records] == ["@robot 检查xx目录\nB: 这仍是A的正文", "已经找到了"]
    assert [record["sender_id"] for record in records] == ["a", "b"]
    assert session.pending_count == 2
    assert session.read_input(wait=False) == block
    session.acknowledge(block.block_id)
    session.acknowledge(block.block_id)
    assert session.read_input(wait=False) is None


def test_duplicate_ids_are_scoped_to_conversation(caplog):
    session, channel, gateway = session_pair()
    channel.receive("chat", "same", "1")
    channel.receive("chat", "same", "1")
    channel.receive("chat", "same", "2")
    channel.receive("other", "wrong", "3")
    block = session.read_input(wait=False)
    assert block.text.count('"text": "same"') == 2
    assert '"session_id": "other"' in block.text
    session.acknowledge(block.block_id)
    channel.receive("chat", "same", "1")
    assert session.read_input(wait=False) is None
    assert {session for _, session in session.conversations} == {"chat", "other"}


def test_new_arrivals_do_not_rewrite_unacknowledged_block():
    session, channel, gateway = session_pair()
    channel.receive("chat", "old")
    first = session.read_input(wait=False)
    channel.receive("chat", "new")
    assert session.read_input(wait=False) is first
    assert session.pending_count == 2
    with pytest.raises(ValueError):
        session.acknowledge("wrong")
    session.acknowledge(first.block_id)
    second = session.read_input(wait=False)
    assert second.block_id != first.block_id
    assert second.source_texts == ("new",)


def test_confirmation_can_arrive_while_an_old_block_is_unacknowledged():
    session, channel, gateway = session_pair()
    channel.receive("chat", "old")
    first = session.read_input(wait=False)
    channel.receive("chat", "他说 done")
    channel.receive("chat", "补充信息")
    channel.receive("chat", " done ")
    session.wait_for_confirmation("done")
    assert session.read_input(wait=False) is first
    session.acknowledge(first.block_id)
    block = session.read_input(wait=False)
    assert block.source_texts == ("他说 done", "补充信息", " done ")
    gateway.request_stop()
    with pytest.raises(MessageStreamClosed):
        session.wait_for_confirmation("done")


def test_quoted_confirmation_does_not_resume_and_stop_wakes_waiter():
    session, channel, gateway = session_pair()
    channel.receive("chat", "@robot done", name="done")
    entered, done = Event(), Event()
    errors = []
    def wait():
        entered.set()
        try:
            session.wait_for_confirmation("done")
        except MessageStreamClosed as error:
            errors.append(error)
        finally:
            done.set()
    thread = Thread(target=wait)
    thread.start()
    assert entered.wait(2)
    assert not done.wait(.05)
    gateway.request_stop()
    assert done.wait(2)
    thread.join(2)
    assert errors


def test_same_names_are_disambiguated_and_absent_names_use_ids():
    session, channel, gateway = session_pair()
    channel.receive("chat", "one", sender="a", name="same")
    channel.receive("chat", "two", sender="b", name="same")
    channel.receive("chat", "three", sender="c")
    block = session.read_input(wait=False)
    records = [json.loads(line) for line in block.text.splitlines()[1:]]
    assert [record["sender_id"] for record in records] == ["a", "b", "c"]
    assert [record["sender_name"] for record in records] == ["same", "same", None]


def test_close_reports_unacknowledged_input_without_closing_gateway(caplog):
    session, channel, gateway = session_pair()
    assert session.send_message("unbound") is False
    channel.receive("chat", "pending")
    session.read_input(wait=False)
    session.close()
    session.close()
    assert session.pending_count == 1
    assert "1 unacknowledged" in caplog.text
    with pytest.raises(MessageStreamClosed):
        session.read_input(wait=False)
    assert channel.receive("chat", "still open")
    assert len(gateway.receive_messages(wait=False)) == 1


def test_polling_during_tools_does_not_seal_partial_dialogue():
    session, channel, gateway = session_pair()
    channel.receive("chat", "first")
    assert session.poll_input()
    assert session.pending_block is None
    channel.receive("chat", "second")
    assert session.read_input(wait=False).source_texts == ("first", "second")
