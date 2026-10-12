"""Minimal transport interface; implementations own their SDK lifecycle."""

from abc import ABC, abstractmethod
from collections.abc import Callable

from src.messaging.messages import IncomingMessage, OutgoingMessage

type MessageListener = Callable[[IncomingMessage], bool]


class Channel(ABC):
    @abstractmethod
    def bind_message_listener(self, listener: MessageListener) -> None:
        """Bind before startup. False means the message was not accepted."""

    @abstractmethod
    def send_message(self, message: OutgoingMessage) -> None:
        """Enqueue for transport delivery, or raise if admission fails."""

    @abstractmethod
    def start(self) -> None:
        """Start the owned worker without blocking the caller."""

    @abstractmethod
    def close(self) -> None:
        """Drain accepted outgoing messages and stop the owned worker."""
