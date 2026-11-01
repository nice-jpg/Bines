"""Minimal transport interface; implementations own their SDK lifecycle."""

from abc import ABC, abstractmethod

from src.channel.buffer import MessageBuffer
from src.channel.messages import OutBoundMessage


class Channel(ABC):
    @property
    @abstractmethod
    def channel_id(self) -> str:
        """Stable channel identity for this transport."""

    @abstractmethod
    def bind_buffer(self, buffer: MessageBuffer) -> None:
        """Bind the external input buffer before startup."""

    @abstractmethod
    async def send_message(self, message: OutBoundMessage) -> bool:
        """Deliver one message, retaining its conversation address."""

    @abstractmethod
    async def start(self) -> None:
        """Start transport on the application's event loop."""

    @abstractmethod
    async def close(self) -> None:
        """Stop transport without closing the application's event loop."""
