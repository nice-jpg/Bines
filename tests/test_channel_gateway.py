"""Manager identity, complete batches, independent gateways and closure."""
from threading import Event, Thread

import pytest
from gateway_helpers import GatewayHandle, RecordingChannel
from src.application.gateway_manager import GatewayManager
from src.agent_runtime.messaging.messages import IncomingMessage, MessageStreamClosed, OutgoingMessage


def test_batch_close_preserves_fifo_and_keeps_output_open():
    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    gateway.start()
    assert gateway.receive_messages(wait=False) == []
    assert channel.receive("chat", "first")
    assert channel.receive("chat", "second")
    gateway.request_stop()
    assert not channel.receive("chat", "late")
    assert [m.text for m in gateway.receive_messages(wait=True)] == ["first", "second"]
    with pytest.raises(MessageStreamClosed):
        gateway.receive_messages(wait=False)
    assert gateway.send_message(OutgoingMessage("feishu", "chat", "last notice"))
    gateway.finish_output()
    with pytest.raises(MessageStreamClosed):
        gateway.send_message(OutgoingMessage("feishu", "chat", "late"))
    gateway.join()
    assert channel.output().text == "last notice"
    assert channel.closed


def test_blocking_read_wakes_on_stop_and_rejects_overlap_without_blocking_other_gateway():
    first = GatewayHandle(RecordingChannel())
    other = GatewayHandle(RecordingChannel())
    entered, finished = Event(), Event()
    errors = []
    def receive():
        entered.set()
        try:
            first.receive_messages(wait=True)
        except MessageStreamClosed as error:
            errors.append(error)
        finally:
            finished.set()
    thread = Thread(target=receive)
    thread.start()
    assert entered.wait(2)
    assert not finished.wait(.05)
    with pytest.raises(RuntimeError, match="Overlapping"):
        first.receive_messages(wait=False)
    assert other.receive_messages(wait=False) == []
    first.request_stop()
    assert finished.wait(2)
    thread.join(2)
    assert len(errors) == 1


def test_manager_generates_ids_and_enforces_instance_lifetime():
    manager = GatewayManager.instance()
    assert manager is GatewayManager.instance()
    channel = RecordingChannel()
    first = manager.create(channel)
    second = manager.create(RecordingChannel())
    assert first != second
    with pytest.raises(ValueError, match="already registered"):
        manager.create(channel)
    with pytest.raises(RuntimeError, match="Join"):
        manager.release(first)
    manager.join(first)
    manager.release(first)
    with pytest.raises(LookupError):
        manager.receive_messages(first, wait=False)
    assert manager.receive_messages(second, wait=False) == []


def test_output_backpressure_and_shutdown_wakes_sender(monkeypatch):
    monkeypatch.setattr("src.application.channel_gateway.OUTGOING_CAPACITY", 1)
    monkeypatch.setattr("src.application.channel_gateway.SEND_TIMEOUT_SECONDS", 10.)
    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    gateway.send_message(OutgoingMessage("feishu", "chat", "first"))
    entered, finished = Event(), Event()
    errors = []
    def send():
        entered.set()
        try:
            gateway.send_message(OutgoingMessage("feishu", "chat", "second"))
        except MessageStreamClosed as error:
            errors.append(error)
        finally:
            finished.set()
    thread = Thread(target=send)
    thread.start()
    assert entered.wait(2)
    assert not finished.wait(.05)
    gateway.finish_output()
    assert finished.wait(2)
    thread.join(2)
    gateway.start()
    gateway.join()
    assert channel.output().text == "first"
    assert errors and channel.sent.empty()


def test_channel_identity_and_queue_capacity(monkeypatch):
    monkeypatch.setattr("src.application.channel_gateway.INCOMING_CAPACITY", 1)
    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    assert not channel.listener(IncomingMessage("other", "chat", "1", "spoof", "user"))
    assert channel.receive("chat", "first")
    assert not channel.receive("chat", "second")
    with pytest.raises(ValueError):
        gateway.send_message(OutgoingMessage("other", "chat", "spoof"))
    assert len(gateway.receive_messages(wait=False)) == 1


def test_channel_failure_does_not_affect_other_gateway():
    class BrokenChannel(RecordingChannel):
        def send_message(self, message):
            raise OSError("offline")
    broken = GatewayHandle(BrokenChannel())
    channel = RecordingChannel()
    healthy = GatewayHandle(channel)
    for gateway in (broken, healthy):
        gateway.start()
        gateway.send_message(OutgoingMessage("feishu", "chat", "message"))
        gateway.finish_output()
        gateway.join()
    assert channel.output().text == "message"
