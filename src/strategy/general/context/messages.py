"""Metadata identifying page manuals and archived page activity."""

from typing import Final

from langchain_core.messages import BaseMessage
from pydantic import TypeAdapter, ValidationError
from typing_extensions import TypedDict

from src.agent_messages.text import message_additional_kwargs
from src.common.utils.validation import is_string_mapping

MANUAL_CONTEXT_KIND: Final = "page_manual"
MANUAL_PLACEHOLDER_CONTEXT_KIND: Final = "page_manual_placeholder"
PAGE_MESSAGE_CONTEXT_KEY: Final = "bines_context"


class PageMessageContext(TypedDict, total=False):
    kind: str
    canonical_path: str
    page_visit_id: str
    sha256: str
    display_path: str
    collected_items: int


_CONTEXT_ADAPTER: TypeAdapter[PageMessageContext] = TypeAdapter(PageMessageContext)


def read_page_message_context(message: object) -> PageMessageContext | None:
    """Read the known metadata fields without changing the original message."""
    additional: object
    if isinstance(message, BaseMessage):
        additional = message_additional_kwargs(message)
    elif is_string_mapping(message):
        additional = message.get("additional_kwargs")
    else:
        return None
    if not is_string_mapping(additional):
        return None
    context: object = additional.get(PAGE_MESSAGE_CONTEXT_KEY)
    if not is_string_mapping(context):
        return None
    try:
        return _CONTEXT_ADAPTER.validate_python(context, strict=True)
    except ValidationError:
        return None
