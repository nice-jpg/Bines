"""Shared graph-state keys for probe lifecycle and merchant collection."""

from __future__ import annotations

from typing import Annotated, Any

from langchain.agents.middleware.types import AgentState
from typing_extensions import NotRequired


def replace_state_value(_current: Any, new: Any) -> Any:
    """Use the latest deterministic runtime value for one graph-state key."""

    return new


PROBE_STATUS = "probe_status"
PROBE_PREMATURE_FINAL_COUNT = "probe_premature_final_count"
PROBE_TARGET_LEAF_PATHS = "probe_target_leaf_paths"
PROBE_VISITED_LEAF_PATHS = "probe_visited_leaf_paths"
PROBE_CONTINUATION_REASON = "probe_continuation_reason"

PROBE_INACTIVE = "inactive"
PROBE_RUNNING = "running"
PROBE_COMPLETION_AUTHORIZED = "completion_authorized"
PROBE_COMPLETED = "completed"

COLLECTION_INITIALIZED = "collection_initialized"
COLLECTION_LATEST_NEW_COUNT = "collection_latest_new_count"
MERCHANT_COLLECTION_DONE = "merchant_collection_done"
COLLECTION_PAGE_VISIT_ID = "collection_page_visit_id"


class ProbeState(AgentState):
    """Thread-scoped lifecycle state shared by cooperating middleware."""

    probe_status: NotRequired[Annotated[str, replace_state_value]]
    probe_premature_final_count: NotRequired[Annotated[int, replace_state_value]]
    probe_target_leaf_paths: NotRequired[Annotated[tuple[str, ...], replace_state_value]]
    probe_visited_leaf_paths: NotRequired[Annotated[tuple[str, ...], replace_state_value]]
    probe_continuation_reason: NotRequired[Annotated[str, replace_state_value]]
    collection_initialized: NotRequired[Annotated[bool, replace_state_value]]
    collection_latest_new_count: NotRequired[Annotated[int, replace_state_value]]
    merchant_collection_done: NotRequired[Annotated[bool, replace_state_value]]
    collection_page_visit_id: NotRequired[Annotated[str, replace_state_value]]


def initial_probe_state(target_leaf_paths: tuple[str, ...]) -> dict[str, Any]:
    """Activate a fresh probe lifecycle for an exact probe request."""

    return {
        PROBE_STATUS: PROBE_RUNNING,
        PROBE_PREMATURE_FINAL_COUNT: 0,
        PROBE_TARGET_LEAF_PATHS: tuple(dict.fromkeys(target_leaf_paths)),
        PROBE_VISITED_LEAF_PATHS: (),
        PROBE_CONTINUATION_REASON: "probe_started",
        **reset_collection_state(),
    }


def reset_collection_state() -> dict[str, Any]:
    """Clear collection completion evidence for a new page visit."""

    return {
        COLLECTION_INITIALIZED: False,
        COLLECTION_LATEST_NEW_COUNT: 0,
        MERCHANT_COLLECTION_DONE: False,
        COLLECTION_PAGE_VISIT_ID: "",
    }


__all__ = [
    "COLLECTION_INITIALIZED",
    "COLLECTION_LATEST_NEW_COUNT",
    "COLLECTION_PAGE_VISIT_ID",
    "MERCHANT_COLLECTION_DONE",
    "PROBE_COMPLETED",
    "PROBE_COMPLETION_AUTHORIZED",
    "PROBE_CONTINUATION_REASON",
    "PROBE_INACTIVE",
    "PROBE_PREMATURE_FINAL_COUNT",
    "PROBE_RUNNING",
    "PROBE_STATUS",
    "PROBE_TARGET_LEAF_PATHS",
    "PROBE_VISITED_LEAF_PATHS",
    "ProbeState",
    "initial_probe_state",
    "replace_state_value",
    "reset_collection_state",
]
