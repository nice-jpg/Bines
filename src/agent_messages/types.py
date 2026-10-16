"""Message input shapes accepted by the LangChain framework boundary."""

from typing import Any

from langchain_core.messages import AnyMessage
from pydantic import TypeAdapter
from typing_extensions import TypedDict

from src.serialization.json import JsonObject, JsonValue
from src.serialization.validation import is_string_mapping, is_value_sequence

type InputMessage = AnyMessage | dict[str, Any]


class GraphInput(TypedDict):
    messages: list[InputMessage]


class MessageUpdate(TypedDict):
    """Additional history messages produced by middleware."""

    messages: list[AnyMessage]


class SerializedFunctionCall(TypedDict, total=False):
    name: str
    arguments: str


class SerializedToolCall(TypedDict, total=False):
    id: str
    tool_call_id: str
    name: str
    args: JsonObject
    function: SerializedFunctionCall


class SerializedHistoryMessage(TypedDict, total=False):
    """Legacy serialized history accepted by the context projection boundary."""

    role: str
    type: str
    id: str
    content: JsonValue
    additional_kwargs: JsonObject
    tool_calls: list[SerializedToolCall]
    tool_call_id: str
    name: str


type HistoryMessage = AnyMessage | SerializedHistoryMessage


_MESSAGE_ADAPTER: TypeAdapter[AnyMessage] = TypeAdapter(AnyMessage)


def read_input_messages(value: object) -> list[InputMessage]:
    """Validate framework messages while retaining supported serialized dictionaries."""
    if not is_value_sequence(value):
        raise TypeError("Messages must be a sequence")
    messages: list[InputMessage] = []
    for item in value:
        if is_string_mapping(item):
            messages.append(dict(item))
        else:
            messages.append(_MESSAGE_ADAPTER.validate_python(item, strict=True))
    return messages
