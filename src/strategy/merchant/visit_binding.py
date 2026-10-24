"""Merchant-owned state activation and cleanup at strategy routing boundaries."""

from src.conductor.state import ConductorState, ConductorUpdate, complete_update, current_visit
from src.strategy.general.state import PageVisit
from src.strategy.merchant.state import (
    MerchantState,
    begin_merchant_visit,
    initial_merchant_state,
)


def activate_merchant_visit(state: ConductorState) -> ConductorUpdate | None:
    visit: PageVisit = current_visit(state)
    if state["merchant"].get("visit_id") == visit["visit_id"]:
        return None
    return complete_update(
        state, {"merchant": begin_merchant_visit(visit), "probe": {"current_merchant": ""}}
    )


def deactivate_merchant_visit(state: ConductorState) -> ConductorUpdate | None:
    initial: MerchantState = initial_merchant_state()
    if state["merchant"] == initial and not state["probe"].get("current_merchant"):
        return None
    return complete_update(state, {"merchant": initial, "probe": {"current_merchant": ""}})
