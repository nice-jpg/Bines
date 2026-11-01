"""Console transport for locally submitted tasks and printed notifications."""

from uuid import uuid4

from src.channel.base import Channel
from src.channel.buffer import MessageBuffer
from src.channel.messages import InBoundMessage, OutBoundMessage


class ConsoleChannel(Channel):
    def __init__(self) -> None:
        self._buffer: MessageBuffer | None = None

    @property
    def channel_id(self) -> str:
        return "console"

    def bind_buffer(self, buffer: MessageBuffer) -> None:
        self._buffer = buffer

    def submit(self, text: str) -> None:
        if self._buffer is None or not self._buffer.submit(
            InBoundMessage(self.channel_id, "main", uuid4().hex, text, "user")
        ):
            raise RuntimeError("Console input was not accepted")

    async def send_message(self, message: OutBoundMessage) -> bool:
        import asyncio

        await asyncio.to_thread(print, f"[{message.session_id}] {message.text}")
        return True

    async def start(self) -> None:
        pass

    async def close(self) -> None:
        self._buffer = None
