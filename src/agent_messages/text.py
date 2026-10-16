"""Validate framework message data before rendering its textual content."""

from collections.abc import Mapping

from langchain_core.messages import BaseMessage

from src.serialization.json import JsonValue, json_value
from src.serialization.validation import is_string_mapping, is_value_sequence


def message_content(message: BaseMessage) -> object:
    return message.content  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


def latest_message_text(state: Mapping[str, object]) -> str:
    """Read the last message, retaining the existing display of content blocks."""
    messages: object = state.get("messages")
    if messages is None:
        return ""
    if not is_value_sequence(messages):
        raise TypeError("Agent messages must be a sequence")
    if not messages:
        return ""
    message: object = messages[-1]
    content: object
    if isinstance(message, BaseMessage):
        content = message_content(message)
    elif is_string_mapping(message):
        content = message.get("content")
    else:
        raise TypeError("Unsupported agent message")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    blocks: JsonValue = json_value(content)
    if not isinstance(blocks, list):
        raise TypeError("Message content must be text or content blocks")
    return str(blocks) if blocks else ""


def message_additional_kwargs(message: BaseMessage) -> Mapping[str, object]:
    return message.additional_kwargs  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


def message_response_metadata(message: BaseMessage) -> Mapping[str, object]:
    return message.response_metadata  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
