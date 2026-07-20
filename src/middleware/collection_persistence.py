"""Persist page-local collection results around device tool calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
import json
from threading import Lock
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain.tools import ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

try:
    from src.middleware.page_context import (
        PAGE_CONTEXT_OVERLAY,
        PAGE_CONTEXT_PATH,
        PAGE_VISIT_ID,
        is_compact_popup_result,
    )
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.page_context import (
        PAGE_CONTEXT_OVERLAY,
        PAGE_CONTEXT_PATH,
        PAGE_VISIT_ID,
        is_compact_popup_result,
    )
    from page_mechanism import CollectorTrainingRegistry, PageMechanism


ITEM_APPEND_TOOLS = {"swipe_up", "swipe_down"}
COLLECTION_XML_TOOL_NAMES = {"tap_in_page", "swipe_up", "swipe_down", "uiautomate"}


@dataclass(frozen=True)
class CollectionProgressRecord:
    """Latest persisted item delta for one concrete page visit."""

    page_visit_id: str
    page_path: str
    latest_new_count: int


class CollectionProgressRegistry:
    """Store collection progress independently for each page visit."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], CollectionProgressRecord] = {}
        self._lock = Lock()

    def update(self, page_visit_id: str, page_path: str, latest_new_count: int) -> None:
        key = (str(page_visit_id or ""), str(page_path or ""))
        if not all(key):
            return
        record = CollectionProgressRecord(
            page_visit_id=key[0],
            page_path=key[1],
            latest_new_count=max(0, int(latest_new_count)),
        )
        with self._lock:
            self._records[key] = record

    def get(self, page_visit_id: str, page_path: str) -> CollectionProgressRecord | None:
        key = (str(page_visit_id or ""), str(page_path or ""))
        with self._lock:
            return self._records.get(key)


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
        self._progress = CollectionProgressRegistry()

    def create_tools(self) -> list[StructuredTool]:
        """Return conductor tools bound to this middleware's progress state."""

        return [
            StructuredTool.from_function(
                func=self.is_done,
                name="is_done",
                description=(
                    "Check whether collection is complete for the current merchant page visit. "
                    "Call only when visible page evidence suggests the product region may be "
                    "exhausted, such as a bottom marker, no obvious movement after a swipe, or "
                    "sustained repeated content. Do not call after ordinary swipes. Returns "
                    "done=false while the latest valid swipe added items or before any valid "
                    "collection swipe; returns done=true only when the latest valid swipe added "
                    "zero new items."
                ),
            )
        ]

    def is_done(self, runtime: ToolRuntime) -> dict[str, Any]:
        """Return deterministic completion state for the current merchant visit."""

        state = runtime.state if isinstance(runtime.state, Mapping) else {}
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        visit_id = str(state.get(PAGE_VISIT_ID) or "")
        base = {
            "done": False,
            "initialized": False,
            "latest_new_count": 0,
            "page_path": current_path,
            "page_visit_id": visit_id,
        }
        if not current_path:
            return {**base, "ok": False, "error_type": "missing_page_context"}
        if not visit_id:
            return {**base, "ok": False, "error_type": "missing_page_visit_id"}
        if not self.mechanism.is_merchant_page(current_path):
            return {**base, "ok": False, "error_type": "is_done_not_available"}
        if not self.training_registry.is_trained(visit_id, current_path):
            return {**base, "ok": False, "error_type": "collector_not_trained"}

        progress = self._progress.get(visit_id, current_path)
        if progress is None:
            return {**base, "ok": True}
        return {
            **base,
            "ok": True,
            "done": progress.latest_new_count == 0,
            "initialized": True,
            "latest_new_count": progress.latest_new_count,
        }

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
        if tool_name == "prepare_collector":
            return self._initialize_trained_merchant(request, result)
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
        visit_id = str(state.get(PAGE_VISIT_ID) or "") if isinstance(state, Mapping) else ""
        if not current_path:
            return result

        collection = self.mechanism.run_page_script(current_path, result.content)
        persistence = self._persist(tool_name, current_path, visit_id, collection)
        self._record_progress(tool_name, current_path, visit_id, persistence)
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

    def _record_progress(
        self,
        tool_name: str,
        current_path: str,
        visit_id: str,
        persistence: Mapping[str, Any],
    ) -> None:
        if tool_name not in ITEM_APPEND_TOOLS:
            return
        if persistence.get("ok") is not True or persistence.get("skipped") is True:
            return
        appended_items = persistence.get("appended_items")
        if isinstance(appended_items, bool) or not isinstance(appended_items, int):
            return
        self._progress.update(visit_id, current_path, appended_items)

    def _persist(
        self,
        tool_name: str,
        current_path: str,
        visit_id: str,
        collection: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        if self.workbook_sink is None:
            return {"ok": True, "skipped": True, "reason": "persistence_disabled"}
        if not self.mechanism.is_merchant_page(current_path):
            return {"ok": True, "skipped": True, "reason": "not_merchant_page"}
        if not self.training_registry.is_trained(visit_id, current_path):
            return {"ok": True, "skipped": True, "reason": "collector_not_trained"}
        if collection is None:
            return {"ok": True, "skipped": True, "reason": "current_page_not_set"}
        record = self.training_registry.record_for(visit_id)
        if record is None or not record.merchant_profile:
            return {"ok": True, "skipped": True, "reason": "missing_merchant_profile"}
        try:
            return self.workbook_sink.persist_run_result(
                run_result=_merge_merchant_profile(collection, record.merchant_profile),
                append_items=tool_name in ITEM_APPEND_TOOLS,
            )
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort around device tools.
            return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}

    def _initialize_trained_merchant(self, request: Any, result: ToolMessage | Any) -> ToolMessage | Any:
        if not isinstance(result, ToolMessage) or getattr(result, "status", "success") == "error":
            return result
        state = getattr(request, "state", None)
        state = state if isinstance(state, Mapping) else {}
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        visit_id = str(state.get(PAGE_VISIT_ID) or "")
        if self.workbook_sink is None:
            persistence = {"ok": True, "skipped": True, "reason": "persistence_disabled"}
        elif self.mechanism.page_policy(current_path).role != "merchant":
            persistence = {"ok": True, "skipped": True, "reason": "not_merchant_page"}
        elif not self.training_registry.is_trained(visit_id, current_path):
            persistence = {"ok": True, "skipped": True, "reason": "collector_not_trained"}
        else:
            record = self.training_registry.record_for(visit_id)
            try:
                persistence = self.workbook_sink.persist_run_result(
                    run_result={
                        "ok": True,
                        "result": {
                            "merchant": dict(record.merchant_profile) if record is not None else {},
                            "items": [],
                        },
                    },
                    append_items=False,
                )
            except Exception as exc:  # noqa: BLE001 - persistence must not fail the collector tool.
                persistence = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
        try:
            payload = json.loads(result.content) if isinstance(result.content, str) else {}
        except json.JSONDecodeError:
            payload = {"collector_result": result.content}
        if not isinstance(payload, dict):
            payload = {"collector_result": payload}
        payload["persistence"] = persistence
        return _replace_tool_message_content(result, json.dumps(payload, ensure_ascii=False, sort_keys=True))


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


def _merge_merchant_profile(
    collection: Mapping[str, Any],
    merchant_profile: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(collection)
    if collection.get("ok") is not True or not isinstance(collection.get("result"), Mapping):
        return merged
    result = dict(collection["result"])
    result.pop("merchant", None)
    result["merchant"] = dict(merchant_profile)
    merged["result"] = result
    return merged
