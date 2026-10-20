"""Test handles for the ID-addressed production manager."""

from queue import Queue
from threading import current_thread
from uuid import uuid4

from src.application.gateway_manager import GatewayManager
from src.channel.base import Channel
from src.agent_runtime.messaging.messages import IncomingMessage


class RecordingChannel(Channel):
    def __init__(self, channel_id="feishu"):
        self._channel_id = channel_id
        self.sent = Queue()
        self.listener = None
        self.closed = False

    @property
    def channel_id(self):
        return self._channel_id

    def bind_message_listener(self, listener):
        self.listener = listener

    def send_message(self, message):
        self.sent.put((message, current_thread().name))

    def start(self):
        pass

    def close(self):
        self.closed = True

    def receive(self, chat, text, identity=None, sender="user", name=None):
        return self.listener(IncomingMessage(self.channel_id, chat, identity or uuid4().hex, text, sender, name))

    def output(self):
        message, thread = self.sent.get(timeout=5)
        assert thread.startswith("gateway-")
        return message


class GatewayHandle:
    def __init__(self, channel):
        self.manager = GatewayManager.instance()
        self.gateway_id = self.manager.create(channel)

    def start(self):
        self.manager.start(self.gateway_id)

    def request_stop(self):
        self.manager.stop(self.gateway_id)

    def finish_output(self):
        self.manager.finish_output(self.gateway_id)

    def join(self):
        self.manager.join(self.gateway_id)

    def send_message(self, message):
        return self.manager.send_message(self.gateway_id, message)

    def receive_messages(self, *, wait):
        return self.manager.receive_messages(self.gateway_id, wait=wait)
