"""Immutable external-channel envelopes."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SessionKey:
    channel_id: str
    session_id: str


@dataclass(frozen=True)
class InBoundMessage:
    channel_id: str
    session_id: str
    message_id: str
    text: str
    sender_id: str
    sender_name: str | None = None

    @property
    def address(self) -> SessionKey:
        return SessionKey(self.channel_id, self.session_id)


@dataclass(frozen=True)
class OutBoundMessage:
    channel_id: str
    session_id: str
    text: str


@dataclass(frozen=True)
class ChannelBatch:
    batch_id: str
    address: SessionKey
    first_sequence: int
    messages: tuple[InBoundMessage, ...]
