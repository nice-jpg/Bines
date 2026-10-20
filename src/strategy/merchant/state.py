"""Merchant-visit workflow and collection evidence."""

from typing import Final, Literal

from typing_extensions import TypedDict

from src.strategy.general.state import PageVisit

MERCHANT: Final = "merchant"
MERCHANT_AWAITING_TRAINING: Final = "awaiting_training"
MERCHANT_READY: Final = "ready"
MERCHANT_TRAVERSING: Final = "traversing"
MERCHANT_COMPLETED: Final = "completed"

type MerchantPhase = Literal["inactive", "awaiting_training", "ready", "traversing", "completed"]


class CollectionEvidence(TypedDict, total=False):
    initialized: bool
    latest_new_count: int
    consecutive_empty_swipes: int
    last_swipe: str
    traversal: str


class MerchantState(TypedDict, total=False):
    visit_id: str
    phase: MerchantPhase
    training_version: str
    entry_taps: int
    evidence: CollectionEvidence


def initial_evidence() -> CollectionEvidence:
    return {
        "initialized": False,
        "latest_new_count": 0,
        "consecutive_empty_swipes": 0,
        "last_swipe": "",
        "traversal": "",
    }


def initial_merchant_state() -> MerchantState:
    return {
        "visit_id": "",
        "phase": "inactive",
        "training_version": "",
        "entry_taps": 0,
        "evidence": initial_evidence(),
    }


def begin_merchant_visit(visit: PageVisit) -> MerchantState:
    return {**initial_merchant_state(), "visit_id": visit["visit_id"], "phase": MERCHANT_AWAITING_TRAINING}


def is_merchant_visit(visit: PageVisit) -> bool:
    return visit["strategy"] == MERCHANT
