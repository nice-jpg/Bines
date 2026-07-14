"""Persist page-local collection results around device tool calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
import json
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

try:
    from src.page_mechanism import CollectorTrainingRegistry, PageRunHook
    from src.tools.event_hub import XML_RESULT_TOOL_NAMES
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from page_mechanism import CollectorTrainingRegistry, PageRunHook
    from tools.event_hub import XML_RESULT_TOOL_NAMES


ITEM_APPEND_TOOLS = {"swipe_up", "swipe_down"}


class CollectionPersistenceMiddleware(AgentMiddleware):
    """Run page-local scripts and persist collection output after XML tools."""

    def __init__(
        self,
        *,
        page_run_hook: PageRunHook,
        training_registry: CollectorTrainingRegistry,
        workbook_sink: Any | None = None,
    ) -> None:
        self.page_run_hook = page_run_hook
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
        if tool_name not in XML_RESULT_TOOL_NAMES:
            return result
        if not isinstance(result, ToolMessage):
            return result
        if getattr(result, "status", "success") == "error":
            return result
        if not isinstance(result.content, str):
            return result
        if not self.page_run_hook.current_path:
            return result

        collection = self.page_run_hook.run_current_page(result.content)
        persistence = self._persist(tool_name, collection)
        return _replace_tool_message_content(
            result,
            json.dumps(
                {
                    "xml": result.content,
                    "page_path": self.page_run_hook.current_path,
                    "collection": collection,
                    "persistence": persistence,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )

    def _persist(self, tool_name: str, collection: Mapping[str, Any] | None) -> dict[str, Any]:
        current_path = self.page_run_hook.current_path
        if self.workbook_sink is None:
            return {"ok": True, "skipped": True, "reason": "persistence_disabled"}
        if not self.page_run_hook.mechanism.is_merchant_page(current_path):
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
