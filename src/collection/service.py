"""Shared collection execution, admission and progress, independent of tool middleware."""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any
import logging

from src.probe_state import (
    COLLECTION_CONSECUTIVE_EMPTY_SWIPES, COLLECTION_INITIALIZED,
    COLLECTION_LAST_SWIPE, COLLECTION_LATEST_NEW_COUNT, COLLECTION_PAGE_VISIT_ID,
    COLLECTION_VISIT_SUMMARIES, MERCHANT_COLLECTION_DONE, PROBE_COMPLETED_MERCHANTS,
    merchant_identity,
)
from src.page_mechanism import CollectorTrainingRegistry, PageMechanism
from src.page_view import ReadOnlyPageView
from src.collection_audit import sample_collection
from src.tools.content_provider import CollectionWorkbookSink
from src.tools.event_hub import EventHub

PAGE_CONTEXT_PATH = "page_context_path"
PAGE_VISIT_ID = "page_visit_id"
ITEM_APPEND_TOOLS = {"swipe_up", "swipe_up_long", "swipe_down"}
logger = logging.getLogger(__name__)


class StepStatus(StrEnum):
    SUCCESS = "success"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


@dataclass(frozen=True)
class CollectionStepResult:
    status: StepStatus
    update: dict[str, Any]
    new_count: int = 0
    error: str = ""


@dataclass(frozen=True)
class CompletionResult:
    payload: dict[str, Any]
    update: dict[str, Any]

    @property
    def done(self) -> bool:
        return self.payload["done"]


class CollectionService:
    def __init__(self, mechanism: PageMechanism, training_registry: CollectorTrainingRegistry,
                 workbook_sink: CollectionWorkbookSink | None, event_hub: EventHub | None):
        self.mechanism = mechanism
        self.training_registry = training_registry
        self.workbook_sink = workbook_sink
        self.event_hub = event_hub

    def completion(self, state: Mapping[str, Any]) -> CompletionResult:
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        visit_id = str(state.get(PAGE_VISIT_ID) or "")
        base = {
            "done": False,
            "initialized": False,
            "latest_new_count": 0,
            "consecutive_empty_swipes": 0,
            "page_path": current_path,
            "page_visit_id": visit_id,
        }
        error_type = ""
        if not current_path:
            error_type = "missing_page_context"
        elif not visit_id:
            error_type = "missing_page_visit_id"
        elif not self.mechanism.is_merchant_page(current_path):
            error_type = "is_done_not_available"
        elif not self.training_registry.is_trained(visit_id, current_path):
            error_type = "collector_not_trained"
        else:
            try:
                contract = self.mechanism.collection_contract(current_path)
                record = self.training_registry.record_for(visit_id)
                if contract is None or (record.contract_fingerprint and record.contract_fingerprint != contract.fingerprint):
                    error_type = "collection_contract_requires_training"
            except (ValueError, FileNotFoundError):
                error_type = "collection_contract_requires_training"

        initialized = (
            state.get(COLLECTION_INITIALIZED) is True
            and str(state.get(COLLECTION_PAGE_VISIT_ID) or "") == visit_id
        )
        latest_new_count = (
            max(0, int(state.get(COLLECTION_LATEST_NEW_COUNT, 0)))
            if initialized
            else 0
        )
        consecutive_empty_swipes = (
            max(0, int(state.get(COLLECTION_CONSECUTIVE_EMPTY_SWIPES, 0)))
            if initialized
            else 0
        )
        done = bool(not error_type and initialized and consecutive_empty_swipes >= 3)
        payload = {
            **base,
            "ok": not error_type,
            "done": done,
            "initialized": initialized,
            "latest_new_count": latest_new_count,
            "consecutive_empty_swipes": consecutive_empty_swipes,
        }
        if error_type:
            payload["error_type"] = error_type
        update = {MERCHANT_COLLECTION_DONE: done} if not error_type else {}
        if done:
            record = self.training_registry.record_for(visit_id)
            name = merchant_identity(str((record.merchant_profile or {}).get("name") or ""))
            if name:
                completed = dict(state.get(PROBE_COMPLETED_MERCHANTS) or {})
                completed[current_path] = tuple(dict.fromkeys((*completed.get(current_path, ()), name)))
                update[PROBE_COMPLETED_MERCHANTS] = completed
        return CompletionResult(payload, update)

    def collect(self, state: Mapping[str, Any], snapshot: dict, tool_name: str,
                arguments: Mapping[str, Any]) -> CollectionStepResult:
        current_path = str(state.get(PAGE_CONTEXT_PATH) or "")
        visit_id = str(state.get(PAGE_VISIT_ID) or "")
        contract = None
        try:
            contract = self.mechanism.collection_contract(current_path)
            if contract is None:
                raise ValueError("Missing collection contract")
            record = self.training_registry.record_for(visit_id)
            if record.contract_fingerprint and record.contract_fingerprint != contract.fingerprint:
                raise ValueError("Collection contract changed; training is stale")
            collection = self.mechanism.run_page_script(current_path, ReadOnlyPageView(snapshot))
        except Exception as exc:
            collection = {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}
        incomplete = []
        admitted = collection
        if collection.get("ok") is True:
            items = collection["result"].get("items")
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                collection = {"ok": False, "error_type": "InvalidItems"}
            else:
                incomplete = [{"index": index, "missing": contract.missing(item)} for index, item in enumerate(items) if contract.missing(item)]
                admitted = {**collection, "result": {**collection["result"], "items": [item for item in items if not contract.missing(item)]}}
        persistence = (self._persist(tool_name, current_path, visit_id, admitted)
                       if collection.get("ok") is True else {"ok": False, "skipped": True})
        if tool_name in ITEM_APPEND_TOOLS:
            record = self.training_registry.record_for(visit_id)
            sample_collection(self.event_hub, Path(__file__).resolve().parents[2] / "workspace/collection_audits",
                current_path, visit_id, snapshot, collection,
                {"incomplete": incomplete, "persistence": persistence}, contract, {**arguments, "tool": tool_name},
                record.script_fingerprint if record else "")
        if collection.get("ok") is not True:
            return self._collection_failed(current_path, visit_id, collection)
        if persistence.get("ok") is not True:
            return self._collection_failed(current_path, visit_id, persistence)
        progress_update = self._progress_update(
            tool_name,
            current_path,
            visit_id,
            persistence,
            state,
        )
        if tool_name in ITEM_APPEND_TOOLS:
            progress_update[COLLECTION_LAST_SWIPE] = "swipe_down" if tool_name == "swipe_down" else "swipe_up"
        if incomplete:
            progress_update.update({COLLECTION_CONSECUTIVE_EMPTY_SWIPES: 0, MERCHANT_COLLECTION_DONE: False})
        skipped = persistence.get("skipped") is True
        return CollectionStepResult(
            status=StepStatus.FAILED if skipped else (StepStatus.INCOMPLETE if incomplete else StepStatus.SUCCESS),
            update=progress_update,
            new_count=_non_negative_int(persistence.get("appended_items")),
            error=str(persistence.get("reason") or "collection_skipped") if skipped else "",
        )

    def _collection_failed(self, current_path: str, visit_id: str,
                           failure: Mapping[str, Any]) -> CollectionStepResult:
        logger.warning("Silent collection failed for %s (%s): %s", current_path, visit_id, failure)
        return CollectionStepResult(StepStatus.FAILED, {
            COLLECTION_CONSECUTIVE_EMPTY_SWIPES: 0, MERCHANT_COLLECTION_DONE: False,
        }, error=str(failure.get("error_type") or failure.get("reason") or "collection_failed"))

    def _progress_update(
        self,
        tool_name: str,
        current_path: str,
        visit_id: str,
        persistence: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> dict[str, Any]:
        if tool_name not in ITEM_APPEND_TOOLS:
            return {}
        if persistence.get("ok") is not True or persistence.get("skipped") is True:
            return {}
        appended_items = persistence.get("appended_items")
        if isinstance(appended_items, bool) or not isinstance(appended_items, int):
            return {}
        same_visit = str(state.get(COLLECTION_PAGE_VISIT_ID) or "") == visit_id
        previous_empty_swipes = (
            max(0, int(state.get(COLLECTION_CONSECUTIVE_EMPTY_SWIPES, 0)))
            if same_visit and state.get(COLLECTION_INITIALIZED) is True
            else 0
        )
        new_count = max(0, appended_items)
        consecutive_empty_swipes = previous_empty_swipes + 1 if new_count == 0 else 0
        summaries = _collection_summaries(state)
        previous_summary = summaries.get(visit_id, {})
        record = self.training_registry.record_for(visit_id)
        merchant_profile = (
            dict(record.merchant_profile)
            if record is not None and record.merchant_profile
            else dict(previous_summary.get("merchant_profile") or {})
        )
        summaries[visit_id] = {
            "page_path": current_path,
            "merchant_profile": merchant_profile,
            "collected_items": _non_negative_int(previous_summary.get("collected_items")) + new_count,
            "is_leaf": self.mechanism.is_merchant_page(current_path),
        }
        return {
            COLLECTION_INITIALIZED: True,
            COLLECTION_LATEST_NEW_COUNT: new_count,
            COLLECTION_CONSECUTIVE_EMPTY_SWIPES: consecutive_empty_swipes,
            COLLECTION_PAGE_VISIT_ID: visit_id,
            MERCHANT_COLLECTION_DONE: False,
            COLLECTION_VISIT_SUMMARIES: summaries,
        }

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
                contract=self.mechanism.collection_contract(current_path),
            )
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort around device tools.
            return {"ok": False, "error_type": type(exc).__name__, "error": str(exc)}

    def initialize(self, state: Mapping[str, Any]) -> CollectionStepResult:
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
        if persistence.get("ok") is not True:
            return self._collection_failed(current_path, visit_id, persistence)
        record = self.training_registry.record_for(visit_id)
        if (
            record is None
            or not record.merchant_profile
            or not current_path
            or not visit_id
            or not self.training_registry.is_trained(visit_id, current_path)
        ):
            return CollectionStepResult(StepStatus.SUCCESS, {})
        summaries = _collection_summaries(state)
        previous_summary = summaries.get(visit_id, {})
        summaries[visit_id] = {
            "page_path": current_path,
            "merchant_profile": dict(record.merchant_profile),
            "collected_items": _non_negative_int(previous_summary.get("collected_items")),
            "is_leaf": self.mechanism.is_merchant_page(current_path),
        }
        return CollectionStepResult(StepStatus.SUCCESS, {COLLECTION_VISIT_SUMMARIES: summaries})


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


def _collection_summaries(state: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = state.get(COLLECTION_VISIT_SUMMARIES)
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(visit_id): dict(summary)
        for visit_id, summary in raw.items()
        if isinstance(summary, Mapping)
    }


def _non_negative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, value)
