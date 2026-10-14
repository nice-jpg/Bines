"""Validated fields consumed from the Codex WebSocket response protocol."""

from typing import NotRequired

from pydantic import TypeAdapter
from typing_extensions import TypedDict

from src.json_types import JsonValue


class ResponseText(TypedDict, total=False):
    type: str
    text: str


class ResponseItem(TypedDict, total=False):
    type: str
    id: str
    call_id: str
    item_id: str
    name: str
    arguments: JsonValue
    input: str
    output_text: str
    text: str
    content: list[ResponseText]
    summary: list[ResponseText]
    encrypted_content: str


class ResponseUsage(TypedDict, total=False):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    input_tokens_details: dict[str, int]
    output_tokens_details: dict[str, int]


class ResponsePayload(TypedDict, total=False):
    id: str
    store: bool
    output: list[ResponseItem]
    usage: ResponseUsage | None


class ResponseEvent(TypedDict, total=False):
    type: str
    output_index: int
    content_index: int
    delta: str
    text: str
    item: ResponseItem
    response: ResponsePayload


class ResponseMetadata(TypedDict, total=False):
    usage: ResponseUsage
    token_usage: ResponseUsage
    response_id: str
    response_store: bool


class UsageSummary(TypedDict):
    responses: int
    totals: dict[str, int]
    records: list[ResponseUsage]


class ReasoningInput(TypedDict):
    type: str
    summary: list[ResponseText]
    encrypted_content: str
    content: NotRequired[list[ResponseText]]


_EVENT_ADAPTER: TypeAdapter[ResponseEvent] = TypeAdapter(ResponseEvent)
_USAGE_ADAPTER: TypeAdapter[ResponseUsage] = TypeAdapter(ResponseUsage)


def read_response_event(data: str) -> ResponseEvent:
    return _EVENT_ADAPTER.validate_json(data, strict=True)


def read_response_usage(data: object) -> ResponseUsage:
    return _USAGE_ADAPTER.validate_python(data, strict=True)


class CodexMessageMetadata(TypedDict, total=False):
    reasoning_items: list[ResponseItem]
    custom_tool_call_ids: list[str]


_CODEX_MESSAGE_ADAPTER: TypeAdapter[CodexMessageMetadata] = TypeAdapter(CodexMessageMetadata)


def read_codex_message_metadata(data: object) -> CodexMessageMetadata:
    return _CODEX_MESSAGE_ADAPTER.validate_python(data, strict=True)


class FunctionCallInput(TypedDict):
    type: str
    call_id: str
    name: str
    arguments: str


class CustomCallInput(TypedDict):
    type: str
    call_id: str
    name: str
    input: str


class InputContent(TypedDict):
    type: str
    text: NotRequired[str]
    image_url: NotRequired[str]
    file_id: NotRequired[str]
    detail: NotRequired[str]


class MessageInput(TypedDict):
    type: str
    role: str
    content: list[InputContent]


class FunctionOutput(TypedDict):
    type: str
    call_id: str
    output: str | list[InputContent]


class ReasoningConfig(TypedDict):
    effort: str
    summary: str


class PromptCacheOptions(TypedDict):
    comparison_response_id: str
    ttl: str


type ResponseInput = MessageInput | FunctionOutput | FunctionCallInput | CustomCallInput | ResponseItem


class CreateResponseRequest(TypedDict):
    type: str
    model: str
    instructions: str
    input: list[ResponseInput]
    tools: list[dict[str, JsonValue]]
    tool_choice: dict[str, JsonValue] | str
    parallel_tool_calls: bool
    reasoning: ReasoningConfig | None
    store: bool
    include: list[str]
    prompt_cache_key: str
    previous_response_id: NotRequired[str]
    prompt_cache_options: NotRequired[PromptCacheOptions]


class CodexRequestOptions(TypedDict, total=False):
    tools: list[dict[str, JsonValue]] | None
    tool_choice: dict[str, JsonValue] | str | bool | None
    parallel_tool_calls: bool
    previous_response_id: str | None
    store: bool | None


_OPTIONS_ADAPTER: TypeAdapter[CodexRequestOptions] = TypeAdapter(CodexRequestOptions)


def read_request_options(data: object) -> CodexRequestOptions:
    return _OPTIONS_ADAPTER.validate_python(data, strict=True)
