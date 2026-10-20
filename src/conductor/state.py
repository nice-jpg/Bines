"""Conductor state composition and validated graph boundaries."""

from collections.abc import Mapping
from typing import Final, Literal
from uuid import uuid4

from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AnyMessage, RemoveMessage
from pydantic import TypeAdapter
from typing_extensions import TypedDict

from src.agent_messages.types import GraphInput
from src.common.utils.json import JsonObject
from src.common.utils.validation import is_string_mapping
from src.strategy.general.state import GeneralState, PageVisit, initial_general_state
from src.strategy.merchant.state import (
    MERCHANT_COMPLETED,
    MERCHANT_TRAVERSING,
    MerchantState,
    initial_evidence,
    initial_merchant_state,
)

from .checkpoints import migrate_conductor_checkpoint

PROBE_STARTED: Final = "probe_started"


# Cross-page recovery and continuation reasons
REASON_PAGE_CONFIRMATION: Final = "page_confirmation_required"
REASON_APPLICATION_CLOSE: Final = "application_close_required"
REASON_MERCHANT_INCOMPLETE: Final = "merchant_collection_incomplete"
REASON_UNVISITED_PAGES: Final = "configured_leaf_pages_unvisited"
REASON_NOT_AUTHORIZED: Final = "completion_not_authorized"
REASON_UNFINISHED_MERCHANTS: Final = "unfinished_merchants_require_recovery"
REASON_NOT_RUNNING: Final = "probe_not_running"
REASON_APPLICATION_NOT_OPEN: Final = "application_not_open"
REASON_MERCHANT_ACTIVE: Final = "merchant_page_active"
UNVISITED_LEAF_PREFIX: Final = "unvisited_leaf:"


PROBE_INACTIVE: Final = "inactive"
PROBE_RUNNING: Final = "running"
PROBE_COMPLETION_AUTHORIZED: Final = "completion_authorized"
PROBE_COMPLETED: Final = "completed"


class CollectionVisitSummary(TypedDict):
    """Collection facts retained after the active page context changes."""

    page_path: str
    merchant_profile: JsonObject
    collected_items: int
    is_leaf: bool


class ProbeState(TypedDict, total=False):
    package_name: str
    status: str
    current_merchant: str
    unfinished_merchants: dict[str, dict[str, str]]
    failed_tool_count: int
    run_id: str
    completed_merchants: dict[str, tuple[str, ...]]
    premature_final_count: int
    target_leaf_paths: tuple[str, ...]
    visited_leaf_paths: tuple[str, ...]
    continuation_reason: str
    visit_summaries: dict[str, CollectionVisitSummary]


class ConductorFields(TypedDict):
    general: GeneralState
    merchant: MerchantState
    probe: ProbeState


class ConductorState(ConductorFields, AgentState):
    """State for the conductor graph, not a schema imposed on other engines."""


class ConductorInput(GraphInput, total=False):
    general: GeneralState
    merchant: MerchantState
    probe: ProbeState


class ConductorUpdate(TypedDict, total=False):
    general: GeneralState
    merchant: MerchantState
    probe: ProbeState
    messages: list[AnyMessage | RemoveMessage]
    jump_to: Literal["model", "tools", "end"]


_STATE_ADAPTER: TypeAdapter[ConductorState] = TypeAdapter(ConductorState)
_FIELDS_ADAPTER: TypeAdapter[ConductorFields] = TypeAdapter(ConductorFields)
_UPDATE_ADAPTER: TypeAdapter[ConductorUpdate] = TypeAdapter(ConductorUpdate)


def initial_fields() -> ConductorFields:
    return {"general": initial_general_state(), "merchant": initial_merchant_state(), "probe": {}}


def read_conductor_state(value: object) -> ConductorState:
    if not is_string_mapping(value):
        raise TypeError("Conductor state must be a mapping")
    return _STATE_ADAPTER.validate_python(
        {"messages": [], **initial_fields(), **migrate_conductor_checkpoint(dict(value))}, strict=True
    )


def read_conductor_fields(value: object) -> ConductorFields:
    if not is_string_mapping(value):
        raise TypeError("Conductor fields must be a mapping")
    return _FIELDS_ADAPTER.validate_python(
        {**initial_fields(), **migrate_conductor_checkpoint(dict(value))}, strict=True
    )


def read_conductor_update(value: object) -> ConductorUpdate:
    return _UPDATE_ADAPTER.validate_python(value, strict=True)


def current_visit(state: ConductorFields) -> PageVisit:
    visit: PageVisit | None = state["general"].get("visit")
    return visit if visit is not None else {"visit_id": "", "path": "", "strategy": "general"}


def merchant_identity(name: str) -> str:
    return " ".join(name.split())


def collection_completed(state: ConductorFields | ConductorUpdate) -> bool:
    return state.get("merchant", {}).get("phase") == MERCHANT_COMPLETED


def collection_completion_update(state: ConductorFields, done: bool) -> ConductorUpdate:
    merchant: MerchantState = state["merchant"]
    if not merchant.get("visit_id") or merchant.get("visit_id") != current_visit(state)["visit_id"]:
        return {}
    return {
        "merchant": {
            "phase": MERCHANT_COMPLETED
            if done
            else MERCHANT_TRAVERSING
            if merchant.get("phase") == MERCHANT_COMPLETED
            else merchant.get("phase", "inactive")
        }
    }


def reset_collection_state() -> ConductorUpdate:
    return {"merchant": {"evidence": initial_evidence()}}


def initial_probe_state(target_leaf_paths: tuple[str, ...]) -> ConductorUpdate:
    return {
        "probe": {
            "status": PROBE_RUNNING,
            "current_merchant": "",
            "unfinished_merchants": {},
            "failed_tool_count": 0,
            "run_id": uuid4().hex,
            "completed_merchants": {},
            "premature_final_count": 0,
            "target_leaf_paths": tuple(dict.fromkeys(target_leaf_paths)),
            "visited_leaf_paths": (),
            "continuation_reason": PROBE_STARTED,
            "visit_summaries": {},
        }
    }


def merge_fields(state: ConductorFields, *updates: Mapping[str, object]) -> ConductorFields:
    result: dict[str, object] = dict(state)
    for update in updates:
        for key in ("general", "merchant", "probe"):
            if key not in update:
                continue
            incoming: object = update[key]
            previous: object = result[key]
            if not is_string_mapping(incoming) or not is_string_mapping(previous):
                raise TypeError(f"Invalid state namespace: {key}")
            merged: dict[str, object] = {**previous, **incoming}
            if key == "merchant" and "evidence" in incoming:
                old_evidence: object = previous.get("evidence", {})
                new_evidence: object = incoming["evidence"]
                if not is_string_mapping(old_evidence) or not is_string_mapping(new_evidence):
                    raise TypeError("Invalid merchant evidence")
                merged["evidence"] = {**old_evidence, **new_evidence}
            result[key] = merged
    return read_conductor_fields(result)


def complete_update(state: ConductorFields, update: Mapping[str, object]) -> ConductorUpdate:
    merged: ConductorFields = merge_fields(state, update)
    result: dict[str, object] = dict(update)
    for key in ("general", "merchant", "probe"):
        if key in update:
            result[key] = merged[key]
    return read_conductor_update(result)


def combine_updates(*updates: Mapping[str, object]) -> ConductorUpdate:
    """Combine namespace patches inside one transaction, before completion against state."""
    result: dict[str, object] = {}
    touched: set[str] = set()
    for update in updates:
        touched.update(key for key in ("general", "merchant", "probe") if key in update)
        result.update(
            {key: value for key, value in update.items() if key not in {"general", "merchant", "probe"}}
        )
    # Do not emit factory defaults: this is a patch until complete_update is called.
    for key in touched:
        combined: dict[str, object] = {}
        for update in updates:
            raw: object = update.get(key)
            if is_string_mapping(raw):
                if key == "merchant" and "evidence" in raw:
                    evidence: object = combined.get("evidence", {})
                    incoming: object = raw["evidence"]
                    if not is_string_mapping(evidence) or not is_string_mapping(incoming):
                        raise TypeError("Invalid merchant evidence")
                    combined = {**combined, **raw, "evidence": {**evidence, **incoming}}
                else:
                    combined.update(raw)
        result[key] = combined
    return read_conductor_update(result)
