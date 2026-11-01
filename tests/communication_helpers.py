"""A gateway and engine-backed inbox used by communication tests."""

from src.agent_runtime.middleware.communication import ConversationInputMiddleware, read_pending_input

from uuid import uuid4
from gateway_helpers import GatewayHandle
from src.channel.base import Channel
from src.channel.messages import InBoundMessage
from engine_helpers import ScriptedEngine, Model
from langchain_core.messages import AIMessage


class InputChannel(Channel):
    def __init__(self, sender=lambda _: True):
        self.sender = sender
        self.sent = []

    @property
    def channel_id(self):
        return "test"

    def bind_buffer(self, buffer):
        self.listener = buffer.submit

    def send_message(self, message):
        self.sent.append(message)
        self.sender(message.text)

    def start(self):
        pass

    def close(self):
        pass

    def receive(self, text, conversation="conversation"):
        return self.listener(InBoundMessage("test", conversation, uuid4().hex, text, "user"))


class QueueContext:
    def __init__(self, sender=lambda _: True):
        self.channel = InputChannel(sender)
        self.gateway = GatewayHandle(self.channel)
        self.engine = ScriptedEngine(
            model=Model(replies=[AIMessage(content="unused")]), handle=self.gateway.handle
        )

    def receive(self, text):
        return self.channel.receive(text)

    def close(self):
        self.gateway.request_stop()

    @property
    def pending(self):
        return bool(read_pending_input(self.engine.conversation, {}))
