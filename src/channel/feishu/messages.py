"""Message parsing helpers for the Feishu channel."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping


@dataclass(frozen=True)
class IncomingMessage:
    chat_id: str
    message_id: str
    chat_type: str
    sender_open_id: str
    text: str


@dataclass(frozen=True)
class ParsedMessage:
    incoming: IncomingMessage | None = None
    error_text: str | None = None

    @property
    def ok(self) -> bool:
        return self.incoming is not None


def parse_message_event(data: Any) -> ParsedMessage:
    """Parse a Feishu ``im.message.receive_v1`` event into a text message."""

    event = _get(data, "event")
    message = _get(event, "message")
    message_type = str(_get(message, "message_type") or "")
    if message_type != "text":
        return ParsedMessage(error_text="Unsupported message type. Please send a text message.")

    content = _get(message, "content")
    try:
        text = json.loads(str(content or "{}")).get("text", "")
    except json.JSONDecodeError:
        return ParsedMessage(error_text="Failed to parse text message content.")

    text = str(text or "").strip()
    if not text:
        return ParsedMessage(error_text="Text message is empty.")

    sender = _get(event, "sender")
    sender_id = _get(sender, "sender_id")
    return ParsedMessage(
        incoming=IncomingMessage(
            chat_id=str(_get(message, "chat_id") or ""),
            message_id=str(_get(message, "message_id") or ""),
            chat_type=str(_get(message, "chat_type") or ""),
            sender_open_id=str(_get(sender_id, "open_id") or ""),
            text=text,
        )
    )


def _get(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)
