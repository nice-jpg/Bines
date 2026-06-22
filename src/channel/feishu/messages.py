"""Message parsing helpers for the Feishu channel."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any, Mapping


@dataclass(frozen=True)
class MessageTarget:
    chat_id: str
    message_id: str
    chat_type: str


@dataclass(frozen=True)
class IncomingMessage:
    message_id: str
    root_id: str
    parent_id: str
    chat_id: str
    chat_type: str
    message_type: str
    create_time: str
    update_time: str
    sender_open_id: str
    sender_union_id: str
    sender_user_id: str
    text: str = ""
    mentions: list[dict[str, Any]] = field(default_factory=list)
    raw_event: Any | None = None
    raw_message: Any | None = None
    raw_sender: Any | None = None

    @property
    def target(self) -> MessageTarget:
        return MessageTarget(
            chat_id=self.chat_id,
            message_id=self.message_id,
            chat_type=self.chat_type,
        )


@dataclass(frozen=True)
class ParsedMessage:
    incoming: IncomingMessage | None = None
    error_text: str | None = None

    @property
    def ok(self) -> bool:
        return self.incoming is not None and self.error_text is None


def parse_message_event(data: Any) -> ParsedMessage:
    """Parse a Feishu ``im.message.receive_v1`` event without dropping metadata."""

    event = _get(data, "event")
    message = _get(event, "message")
    sender = _get(event, "sender")
    incoming = _incoming_from_parts(event, message, sender)

    if incoming.message_type != "text":
        return ParsedMessage(
            incoming=incoming,
            error_text="Unsupported message type. Please send a text message.",
        )

    content = _get(message, "content")
    try:
        text = json.loads(str(content or "{}")).get("text", "")
    except json.JSONDecodeError:
        return ParsedMessage(
            incoming=incoming,
            error_text="Failed to parse text message content.",
        )

    text = str(text or "").strip()
    if not text:
        return ParsedMessage(incoming=incoming, error_text="Text message is empty.")

    return ParsedMessage(incoming=_replace_text(incoming, text))


def _incoming_from_parts(event: Any, message: Any, sender: Any) -> IncomingMessage:
    sender_id = _get(sender, "sender_id")
    return IncomingMessage(
        message_id=str(_get(message, "message_id") or ""),
        root_id=str(_get(message, "root_id") or ""),
        parent_id=str(_get(message, "parent_id") or ""),
        chat_id=str(_get(message, "chat_id") or ""),
        chat_type=str(_get(message, "chat_type") or ""),
        message_type=str(_get(message, "message_type") or ""),
        create_time=str(_get(message, "create_time") or ""),
        update_time=str(_get(message, "update_time") or ""),
        sender_open_id=str(_get(sender_id, "open_id") or ""),
        sender_union_id=str(_get(sender_id, "union_id") or ""),
        sender_user_id=str(_get(sender_id, "user_id") or ""),
        mentions=_normalize_mentions(_get(message, "mentions")),
        raw_event=event,
        raw_message=message,
        raw_sender=sender,
    )


def _replace_text(message: IncomingMessage, text: str) -> IncomingMessage:
    return IncomingMessage(
        message_id=message.message_id,
        root_id=message.root_id,
        parent_id=message.parent_id,
        chat_id=message.chat_id,
        chat_type=message.chat_type,
        message_type=message.message_type,
        create_time=message.create_time,
        update_time=message.update_time,
        sender_open_id=message.sender_open_id,
        sender_union_id=message.sender_union_id,
        sender_user_id=message.sender_user_id,
        text=text,
        mentions=message.mentions,
        raw_event=message.raw_event,
        raw_message=message.raw_message,
        raw_sender=message.raw_sender,
    )


def _normalize_mentions(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if not isinstance(value, list):
        return []
    return [_to_plain_dict(item) for item in value]


def _to_plain_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): _to_plain_value(item) for key, item in value.items()}
    result: dict[str, Any] = {}
    for key in dir(value):
        if key.startswith("_"):
            continue
        item = getattr(value, key)
        if callable(item):
            continue
        result[key] = _to_plain_value(item)
    return result


def _to_plain_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_to_plain_value(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _to_plain_value(item) for key, item in value.items()}
    return _to_plain_dict(value)


def _get(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)
