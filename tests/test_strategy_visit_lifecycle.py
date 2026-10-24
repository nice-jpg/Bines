"""Routing owns strategy state synchronization before business hooks run."""

import asyncio
from copy import deepcopy

import pytest
from langchain.agents.middleware.types import AgentMiddleware

from page_strategy_helpers import state
from src.agent_runtime.strategy.middleware import StrategyDispatchMiddleware
from src.agent_runtime.strategy.registration import StrategyRegistration
from src.agent_runtime.strategy.registry import StrategyRegistry
from src.conductor.state import ConductorState, current_visit
from src.strategy.general.state import GeneralState, initial_general_state
from src.strategy.general.strategy import GeneralStrategy
from src.strategy.merchant.state import MerchantState, initial_merchant_state
from src.strategy.merchant.strategy import MerchantStrategy


def make_router(observed):
    class InspectBinding(AgentMiddleware):
        def before_model(self, current, runtime):
            observed.append(deepcopy(current["merchant"]))
            return None

        async def abefore_model(self, current, runtime):
            return self.before_model(current, runtime)

    registry = StrategyRegistry()
    registry.register(
        StrategyRegistration(
            "general", GeneralStrategy(constraints=[], features=[]), GeneralState, initial_general_state
        )
    )
    registry.register(
        StrategyRegistration(
            "merchant",
            MerchantStrategy(constraints=[InspectBinding()], features=[]),
            MerchantState,
            initial_merchant_state,
        )
    )
    registry.validate_schema(ConductorState)
    return StrategyDispatchMiddleware(
        state_schema=ConductorState,
        registry=registry,
    )


def route(router, current, asynchronous):
    update = (
        asyncio.run(router.abefore_model(current, None))
        if asynchronous
        else router.before_model(current, None)
    )
    if update:
        current.update(update)
    return update


@pytest.mark.parametrize("asynchronous", [False, True])
def test_merchant_binding_precedes_constraints_and_same_visit_is_preserved(asynchronous):
    seen = []
    router = make_router(seen)
    current = state("meituan/美食/商家")
    current["merchant"] = initial_merchant_state()
    current["probe"]["run_id"] = "task"
    route(router, current, asynchronous)
    assert seen[-1]["visit_id"] == current_visit(current)["visit_id"]
    assert seen[-1]["phase"] == "awaiting_training"
    current["merchant"].update(phase="traversing", training_version="trained", entry_taps=1)
    current["merchant"]["evidence"]["latest_new_count"] = 8
    current["probe"]["current_merchant"] = "shop A"
    saved = deepcopy(current)
    assert route(router, current, asynchronous) is None
    assert current == saved
    assert seen[-1] == saved["merchant"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_new_visit_with_same_strategy_resets_binding(asynchronous):
    seen = []
    router = make_router(seen)
    current = state("meituan/美食/商家")
    current["merchant"].update(phase="completed", training_version="old", entry_taps=1)
    current["merchant"]["evidence"]["initialized"] = True
    current["probe"]["current_merchant"] = "old shop"
    current["general"]["visit"]["visit_id"] = "new-visit"
    route(router, current, asynchronous)
    assert current["merchant"]["visit_id"] == "new-visit"
    assert current["merchant"]["phase"] == "awaiting_training"
    assert current["merchant"]["training_version"] == ""
    assert current["merchant"]["evidence"]["initialized"] is False
    assert current["probe"]["current_merchant"] == ""
    assert seen[-1] == current["merchant"]


@pytest.mark.parametrize("asynchronous", [False, True])
def test_pending_route_cleans_up_without_general_merchant_logic(asynchronous):
    router = make_router([])
    current = state("meituan/美食/商家")
    current["probe"].update(run_id="task", current_merchant="shop")
    current["probe"]["completed_merchants"] = {"meituan/美食/商家": ("shop",)}
    current["active_strategy"] = "general"
    current["general"]["arrival"] = {
        "candidate_id": "new",
        "path": "meituan",
        "strategy": "general",
        "app_root": "meituan",
        "status": "awaiting_confirmation",
    }
    route(router, current, asynchronous)
    assert current["merchant"] == initial_merchant_state()
    assert current["probe"]["current_merchant"] == ""
    assert current["probe"]["run_id"] == "task"
    assert current["probe"]["completed_merchants"] == {"meituan/美食/商家": ("shop",)}
    assert route(router, current, asynchronous) is None


def test_router_reconstructs_binding_from_each_thread_checkpoint():
    router = make_router([])
    first = state("meituan/美食/商家")
    first["merchant"]["phase"] = "traversing"
    second = state("meituan")
    second["merchant"]["phase"] = "ready"
    second["probe"]["current_merchant"] = "stale"
    saved = deepcopy(first)
    route(router, second, False)
    assert second["merchant"] == initial_merchant_state()
    assert route(router, first, False) is None
    assert first == saved
    assert route(make_router([]), first, False) is None
