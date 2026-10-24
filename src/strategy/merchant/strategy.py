"""Merchant middleware composition and owned visit lifecycle callbacks."""

from src.agent_runtime.strategy.base import Strategy
from src.conductor.state import ConductorState, ConductorUpdate
from src.strategy.merchant.visit_binding import activate_merchant_visit, deactivate_merchant_visit


class MerchantStrategy(Strategy[ConductorState]):
    def activate(self, state: ConductorState) -> dict[str, object] | None:
        update: ConductorUpdate | None = activate_merchant_visit(state)
        return dict(update) if update else None

    def deactivate(self, state: ConductorState) -> dict[str, object] | None:
        update: ConductorUpdate | None = deactivate_merchant_visit(state)
        return dict(update) if update else None
