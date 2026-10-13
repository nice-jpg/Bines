"""Console transport for locally submitted tasks and printed notifications."""

from uuid import uuid4

from src.agent_runtime.messaging.messages import IncomingMessage, OutgoingMessage
from src.channel.base import Channel, MessageListener


class ConsoleChannel(Channel):
    def __init__(self) -> None:
        self._listener: MessageListener | None = None

    @property
    def channel_id(self) -> str:
        return "console"

    def bind_message_listener(self, listener: MessageListener) -> None:
        self._listener = listener

    def submit(self, text: str) -> None:
        if self._listener is None or not self._listener(
            IncomingMessage(self.channel_id, "main", uuid4().hex, text, "user")
        ):
            raise RuntimeError("Console input was not accepted")

    def send_message(self, message: OutgoingMessage) -> None:
        print(f"[NOTIFY] {message.text}")

    def start(self) -> None:
        pass

    def close(self) -> None:
        self._listener = None
