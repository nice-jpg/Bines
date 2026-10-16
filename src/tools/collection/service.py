"""Shared collection execution, admission and progress, independent of tool middleware."""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final

from src.collector.training import CollectorTrainingRecord, CollectorTrainingRegistry
from src.conductor.probe_state import (
    COLLECTION_CONSECUTIVE_EMPTY_SWIPES,
    COLLECTION_INITIALIZED,
    COLLECTION_LAST_SWIPE,
    COLLECTION_LATEST_NEW_COUNT,
    COLLECTION_PAGE_VISIT_ID,
    COLLECTION_VISIT_SUMMARIES,
    MERCHANT_COLLECTION_DONE,
    PROBE_COMPLETED_MERCHANTS,
    CollectionVisitSummary,
    merchant_identity,
)
from src.conductor.state import ConductorFields
from src.device import DeviceOperationService, PageSnapshot, ReadOnlyPageView
from src.page.collection_contract import CollectionContract
from src.page.mechanism import PageMechanism
from src.page.script_types import ScriptResult, ScriptSuccess
from src.page.state import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
from src.serialization.json import JsonObject, JsonValue
from src.tools.collection.audit import sample_collection
from src.tools.collection.types import CompletionPayload, IncompleteRow
from src.tools.workbooks.excel import CollectionWorkbookSink
from src.tools.workbooks.types import PersistenceResult

ITEM_APPEND_TOOLS: Final[set[str]] = {"swipe_up", "swipe_up_long", "swipe_down"}
logger: logging.Logger = logging.getLogger(__name__)


class StepStatus(StrEnum):
    SUCCESS = "success"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


@dataclass(frozen=True)
class CollectionStepResult:
    status: StepStatus
    update: ConductorFields
    new_count: int = 0
    error: str = ""


@dataclass(frozen=True)
class CompletionResult:
    payload: CompletionPayload
    update: ConductorFields

    @property
    def done(self) -> bool:
        return self.payload["done"]


class CollectionService:
    def __init__(
        self,
        mechanism: PageMechanism,
        training_registry: CollectorTrainingRegistry,
        workbook_sink: CollectionWorkbookSink | None,
        event_hub: DeviceOperationService | None,
    ) -> None:
        self.mechanism: PageMechanism = mechanism
        self.training_registry: CollectorTrainingRegistry = training_registry
        self.workbook_sink: CollectionWorkbookSink | None = workbook_sink
        self.event_hub: DeviceOperationService | None = event_hub

    def completion(self, state: ConductorFields) -> CompletionResult:
        current_path: str = state.get(PAGE_CONTEXT_PATH, "")
        visit_id: str = state.get(PAGE_VISIT_ID, "")
        error_type: str = ""
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
                contract: CollectionContract | None = self.mechanism.collection_contract(current_path)
                record: CollectorTrainingRecord | None = self.training_registry.record_for(visit_id)
                if (
                    contract is None
                    or record is None
                    or (record.contract_fingerprint and record.contract_fingerprint != contract.fingerprint)
                ):
                    error_type = "collection_contract_requires_training"
            except (ValueError, FileNotFoundError):
                error_type = "collection_contract_requires_training"

        initialized: bool = (
            state.get(COLLECTION_INITIALIZED) is True and state.get(COLLECTION_PAGE_VISIT_ID, "") == visit_id
        )
        latest_new_count: int = max(0, int(state.get(COLLECTION_LATEST_NEW_COUNT, 0))) if initialized else 0
        consecutive_empty_swipes: int = (
            max(0, int(state.get(COLLECTION_CONSECUTIVE_EMPTY_SWIPES, 0))) if initialized else 0
        )
        done: bool = bool(not error_type and initialized and consecutive_empty_swipes >= 3)
        payload: CompletionPayload = {
            "page_path": current_path,
            "page_visit_id": visit_id,
            "ok": not error_type,
            "done": done,
            "initialized": initialized,
            "latest_new_count": latest_new_count,
            "consecutive_empty_swipes": consecutive_empty_swipes,
        }
        if error_type:
            payload["error_type"] = error_type
        update: ConductorFields = {MERCHANT_COLLECTION_DONE: done} if not error_type else {}
        if done:
            record = self.training_registry.record_for(visit_id)
            raw_name: JsonValue = record.merchant_profile.get("name") if record is not None else None
            name: str = merchant_identity(raw_name) if isinstance(raw_name, str) else ""
            if name:
                completed: dict[str, tuple[str, ...]] = dict(state.get(PROBE_COMPLETED_MERCHANTS) or {})
                completed[current_path] = tuple(dict.fromkeys((*completed.get(current_path, ()), name)))
                update[PROBE_COMPLETED_MERCHANTS] = completed
        return CompletionResult(payload, update)

    def collect(
        self,
        state: ConductorFields,
        snapshot: PageSnapshot,
        tool_name: str,
        arguments: Mapping[str, JsonValue],
    ) -> CollectionStepResult:
        current_path: str = state.get(PAGE_CONTEXT_PATH, "")
        visit_id: str = state.get(PAGE_VISIT_ID, "")
        contract: CollectionContract | None = None
        collection: ScriptResult
        try:
            contract = self.mechanism.collection_contract(current_path)
            if contract is None:
                raise ValueError("Missing collection contract")
            record: CollectorTrainingRecord | None = self.training_registry.record_for(visit_id)
            if record is None:
                raise ValueError("Missing collector training record")
            if record.contract_fingerprint and record.contract_fingerprint != contract.fingerprint:
                raise ValueError("Collection contract changed; training is stale")
            collection = self.mechanism.run_page_script(current_path, ReadOnlyPageView(snapshot))
        except Exception as exc:
            collection = {
                "ok": False,
                "canonical_path": current_path,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": "",
            }
        incomplete: list[IncompleteRow] = []
        admitted: ScriptResult = collection
        if collection["ok"] is True:
            items: JsonValue = collection["result"].get("items")
            rows: list[JsonObject] = []
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict):
                        break
                    rows.append(item)
            if not isinstance(items, list) or len(rows) != len(items) or contract is None:
                collection = {
                    "ok": False,
                    "canonical_path": current_path,
                    "error_type": "InvalidItems",
                    "error": "Collection items must be JSON objects",
                    "traceback": "",
                }
            else:
                accepted: list[JsonValue] = []
                for index, item in enumerate(rows):
                    missing: list[str] = contract.missing(item)
                    if missing:
                        incomplete.append({"index": index, "missing": missing})
                    else:
                        accepted.append(item)
                admitted = {
                    "ok": True,
                    "canonical_path": collection["canonical_path"],
                    "result": {**collection["result"], "items": accepted},
                }
        persistence: PersistenceResult = (
            self._persist(tool_name, current_path, visit_id, admitted)
            if collection["ok"]
            else {"ok": False, "skipped": True}
        )
        if tool_name in ITEM_APPEND_TOOLS:
            record = self.training_registry.record_for(visit_id)
            sample_collection(
                self.event_hub,
                Path(__file__).resolve().parents[2] / "workspace/collection_audits",
                current_path,
                visit_id,
                snapshot,
                collection,
                {"incomplete": incomplete, "persistence": persistence},
                contract,
                {**arguments, "tool": tool_name},
                record.script_fingerprint if record else "",
            )
        if collection["ok"] is False:
            return self._collection_failed(current_path, visit_id, collection)
        if persistence.get("ok") is not True:
            return self._collection_failed(current_path, visit_id, persistence)
        progress_update: ConductorFields = self._progress_update(
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
        skipped: bool = persistence.get("skipped") is True
        return CollectionStepResult(
            status=StepStatus.FAILED
            if skipped
            else (StepStatus.INCOMPLETE if incomplete else StepStatus.SUCCESS),
            update=progress_update,
            new_count=_non_negative_int(persistence.get("appended_items")),
            error=str(persistence.get("reason") or "collection_skipped") if skipped else "",
        )

    def _collection_failed(
        self, current_path: str, visit_id: str, failure: ScriptResult | PersistenceResult
    ) -> CollectionStepResult:
        logger.warning("Silent collection failed for %s (%s): %s", current_path, visit_id, failure)
        return CollectionStepResult(
            StepStatus.FAILED,
            {
                COLLECTION_CONSECUTIVE_EMPTY_SWIPES: 0,
                MERCHANT_COLLECTION_DONE: False,
            },
            error=failure.get("error_type") or failure.get("reason") or "collection_failed",
        )

    def _progress_update(
        self,
        tool_name: str,
        current_path: str,
        visit_id: str,
        persistence: PersistenceResult,
        state: ConductorFields,
    ) -> ConductorFields:
        if tool_name not in ITEM_APPEND_TOOLS:
            return {}
        if persistence.get("ok") is not True or persistence.get("skipped") is True:
            return {}
        appended_items: int | None = persistence.get("appended_items")
        if isinstance(appended_items, bool) or not isinstance(appended_items, int):
            return {}
        same_visit: bool = state.get(COLLECTION_PAGE_VISIT_ID, "") == visit_id
        previous_empty_swipes: int = (
            max(0, int(state.get(COLLECTION_CONSECUTIVE_EMPTY_SWIPES, 0)))
            if same_visit and state.get(COLLECTION_INITIALIZED) is True
            else 0
        )
        new_count: int = max(0, appended_items)
        consecutive_empty_swipes: int = previous_empty_swipes + 1 if new_count == 0 else 0
        summaries: dict[str, CollectionVisitSummary] = _collection_summaries(state)
        previous_summary: CollectionVisitSummary | None = summaries.get(visit_id)
        record: CollectorTrainingRecord | None = self.training_registry.record_for(visit_id)
        merchant_profile: dict[str, JsonValue] = (
            dict(record.merchant_profile)
            if record is not None and record.merchant_profile
            else dict(previous_summary["merchant_profile"])
            if previous_summary is not None
            else {}
        )
        summaries[visit_id] = {
            "page_path": current_path,
            "merchant_profile": merchant_profile,
            "collected_items": (
                _non_negative_int(previous_summary["collected_items"]) if previous_summary is not None else 0
            )
            + new_count,
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
        collection: ScriptResult | None,
    ) -> PersistenceResult:
        if self.workbook_sink is None:
            return {"ok": True, "skipped": True, "reason": "persistence_disabled"}
        if not self.mechanism.is_merchant_page(current_path):
            return {"ok": True, "skipped": True, "reason": "not_merchant_page"}
        if not self.training_registry.is_trained(visit_id, current_path):
            return {"ok": True, "skipped": True, "reason": "collector_not_trained"}
        if collection is None:
            return {"ok": True, "skipped": True, "reason": "current_page_not_set"}
        record: CollectorTrainingRecord | None = self.training_registry.record_for(visit_id)
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

    def initialize(self, state: ConductorFields) -> CollectionStepResult:
        current_path: str = state.get(PAGE_CONTEXT_PATH, "")
        visit_id: str = state.get(PAGE_VISIT_ID, "")
        if self.workbook_sink is None:
            persistence: PersistenceResult = {
                "ok": True,
                "skipped": True,
                "reason": "persistence_disabled",
            }
        elif self.mechanism.page_policy(current_path).role != "merchant":
            persistence = {"ok": True, "skipped": True, "reason": "not_merchant_page"}
        elif not self.training_registry.is_trained(visit_id, current_path):
            persistence = {"ok": True, "skipped": True, "reason": "collector_not_trained"}
        else:
            record: CollectorTrainingRecord | None = self.training_registry.record_for(visit_id)
            try:
                persistence = self.workbook_sink.persist_run_result(
                    run_result={
                        "ok": True,
                        "canonical_path": current_path,
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
        summaries: dict[str, CollectionVisitSummary] = _collection_summaries(state)
        previous_summary: CollectionVisitSummary | None = summaries.get(visit_id)
        summaries[visit_id] = {
            "page_path": current_path,
            "merchant_profile": dict(record.merchant_profile),
            "collected_items": _non_negative_int(previous_summary["collected_items"])
            if previous_summary is not None
            else 0,
            "is_leaf": self.mechanism.is_merchant_page(current_path),
        }
        return CollectionStepResult(StepStatus.SUCCESS, {COLLECTION_VISIT_SUMMARIES: summaries})


def _merge_merchant_profile(
    collection: ScriptResult, merchant_profile: Mapping[str, JsonValue]
) -> ScriptResult:
    if collection["ok"] is False:
        return collection.copy()
    merged: ScriptSuccess = {
        "ok": True,
        "canonical_path": collection["canonical_path"],
        "result": {**collection["result"], "merchant": dict(merchant_profile)},
    }
    return merged


def _collection_summaries(state: ConductorFields) -> dict[str, CollectionVisitSummary]:
    return {
        visit_id: summary.copy() for visit_id, summary in state.get(COLLECTION_VISIT_SUMMARIES, {}).items()
    }


def _non_negative_int(value: int | None) -> int:
    if value is None or isinstance(value, bool):
        return 0
    return max(0, value)
