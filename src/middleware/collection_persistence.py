"""Persist page-local collection results around device tool calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import json
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

try:
    from src.middleware.page_context import PAGE_CONTEXT_OVERLAY, PAGE_CONTEXT_PATH, is_compact_popup_result
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.page_context import PAGE_CONTEXT_OVERLAY, PAGE_CONTEXT_PATH, is_compact_popup_result
    from page_mechanism import CollectorTrainingRegistry, PageMechanism


ITEM_APPEND_TOOLS = {"swipe_up", "swipe_down"}
COLLECTION_XML_TOOL_NAMES = {"tap_in_page", "swipe_up", "swipe_down", "uiautomate"}


class CollectionPersistenceMiddleware(AgentMiddleware):
    """Run page-local scripts and persist collection output after XML tools."""

    def __init__(
        self,
        *,
        mechanism: PageMechanism,
        training_registry: CollectorTrainingRegistry,
        workbook_sink: Any | None = None,
    ) -> None:
        self.mechanism = mechanism
        self.training_registry = training_registry
        self.workbook_sink = workbook_sink

    def wrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], ToolMessage | Any],
    ) -> ToolMessage | Any:
        result = handler(request)
        return self._maybe_wrap_tool_result(request, result)

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        result = await handler(request)
        return self._maybe_wrap_tool_result(request, result)

    def _maybe_wrap_tool_result(self, request: Any, result: ToolMessage | Any) -> ToolMessage | Any:
        tool_name = _tool_name_from_request(request)
        if tool_name not in COLLECTION_XML_TOOL_NAMES:
            return result
        if not isinstance(result, ToolMessage):
            return result
        if getattr(result, "status", "success") == "error":
            return result
        if not isinstance(result.content, str):
            return result
        state = getattr(request, "state", None)
        if isinstance(state, Mapping) and state.get(PAGE_CONTEXT_OVERLAY, False):
            return result
        if is_compact_popup_result(result):
            return result
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "") if isinstance(state, Mapping) else ""
        if not current_path:
            return result

        collection = self.mechanism.run_page_script(current_path, result.content)
        persistence = self._persist(tool_name, current_path, collection)
        return _replace_tool_message_content(
            result,
            json.dumps(
                {
                    "xml": result.content,
                    "page_path": current_path,
                    "collection": collection,
                    "persistence": persistence,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def _persist(
        self,
        tool_name: str,
        current_path: str,
        collection: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if self.workbook_sink is None:
            return {"ok": True, "skipped": True, "reason": "persistence_disabled"}
        if not self.mechanism.is_merchant_page(current_path):
            return {"ok": True, "skipped": True, "reason": "not_merchant_page"}
        if not self.training_registry.is_trained(current_path):
            return {"ok": True, "skipped": True, "reason": "collector_not_trained"}
        if collection is None:
            return {"ok": True, "skipped": True, "reason": "current_page_not_set"}
        try:
            return self.workbook_sink.persist_run_result(
                run_result=collection,
                append_items=tool_name in ITEM_APPEND_TOOLS,
            )
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort around device tools.
            return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}


def _tool_name_from_request(request: Any) -> str:
    tool_call = getattr(request, "tool_call", None)
    if isinstance(tool_call, Mapping):
        return str(tool_call.get("name") or "")
    tool = getattr(request, "tool", None)
    return str(getattr(tool, "name", "") or "")


def _replace_tool_message_content(message: ToolMessage, content: str) -> ToolMessage:
    return ToolMessage(
        content=content,
        tool_call_id=message.tool_call_id,
        name=message.name,
        status=getattr(message, "status", "success"),
        additional_kwargs=dict(getattr(message, "additional_kwargs", {}) or {}),
        response_metadata=dict(getattr(message, "response_metadata", {}) or {}),
    )
