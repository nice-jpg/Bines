"""Custom LangChain middleware for the Bines agent."""

from .captcha_human_in_loop import (
    CAPTCHA_AUTHENTICATION_TOOL_NAME,
    create_captcha_human_in_the_loop_middleware,
)
from .collection_persistence import CollectionPersistenceMiddleware
from .collector_readiness import CollectorReadinessMiddleware
from .device_context_compression import DeviceContextCompressionMiddleware, compact_device_messages
from .page_context import (
    PAGE_CONTEXT_ACTIVE,
    PAGE_CONTEXT_APP_ROOT,
    PAGE_CONTEXT_OVERLAY,
    PAGE_CONTEXT_PATH,
    PAGE_CONTEXT_PENDING_PATH,
    PAGE_COLLECTION_STARTED,
    PAGE_PRODUCT_ENTRY_TAPS,
    PAGE_VISIT_ID,
    PageContextMiddleware,
    initial_page_context_state,
)
from .page_manual import PageManualMiddleware
from .tool_error import ToolErrorMiddleware, make_tool_error_message

__all__ = [
    "CAPTCHA_AUTHENTICATION_TOOL_NAME",
    "CollectionPersistenceMiddleware",
    "CollectorReadinessMiddleware",
    "DeviceContextCompressionMiddleware",
    "PAGE_CONTEXT_ACTIVE",
    "PAGE_CONTEXT_APP_ROOT",
    "PAGE_CONTEXT_OVERLAY",
    "PAGE_CONTEXT_PATH",
    "PAGE_CONTEXT_PENDING_PATH",
    "PAGE_COLLECTION_STARTED",
    "PAGE_PRODUCT_ENTRY_TAPS",
    "PAGE_VISIT_ID",
    "PageContextMiddleware",
    "PageManualMiddleware",
    "ToolErrorMiddleware",
    "compact_device_messages",
    "create_captcha_human_in_the_loop_middleware",
    "initial_page_context_state",
    "make_tool_error_message",
]
