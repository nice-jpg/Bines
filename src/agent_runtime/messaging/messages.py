"""Immutable values crossing the channel/agent boundary."""

from dataclasses import dataclass


@dataclass(frozen=True)
class IncomingMessage:
    channel_id: str
    conversation_id: str
    message_id: str
    text: str
    sender_id: str
    sender_name: str | None = None


@dataclass(frozen=True)
class OutgoingMessage:
    channel_id: str
    conversation_id: str
    text: str


class MessageStreamClosed(Exception):
    """The message stream used by a gateway operation has closed."""
