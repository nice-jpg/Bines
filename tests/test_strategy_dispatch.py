"""Runtime strategies are independent of page names and isolate graph state."""

import asyncio
from types import SimpleNamespace

import pytest
from engine_helpers import Model
from langchain.agents.middleware.types import AgentState
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.types import Command
from typing_extensions import TypedDict

from src.agent_runtime.engine import AgentEngine
from src.agent_runtime.strategy.base import Strategy
from src.agent_runtime.strategy.middleware import StrategyDispatchMiddleware
from src.agent_runtime.strategy.registration import StrategyRegistration
from src.agent_runtime.strategy.registry import StrategyRegistry
from src.conductor.state import complete_update, initial_fields, merge_fields
from src.strategy.merchant.state import begin_merchant_visit


class ReviewState(TypedDict):
    calls: int
    notes: list[str]


class ReviewGraph(AgentState):
    active_strategy: str
    review: ReviewState


def initial_review():
    return {"calls": 0, "notes": []}


class ReviewStrategy(Strategy[ReviewGraph]):
    def before_model(self, state, runtime):
        review = {**state["review"], "calls": state["review"]["calls"] + 1}
        return {"review": review, "messages": [SystemMessage(content="review rules")]}

    async def abefore_model(self, state, runtime):
        return self.before_model(state, runtime)


class ReviewEngine(AgentEngine[ReviewGraph]):
    def _name(self):
        return "review"

    def _system_prompt(self):
        return "base"

    def _create_tools(self):
        return []

    def _strategy_registrations(self):
        return [StrategyRegistration("review", ReviewStrategy(constraints=[], features=[]), ReviewState, initial_review)]

    def _strategy_state_schema(self):
        return ReviewGraph

    def _input_state(self, messages):
        return {"messages": messages, "active_strategy": "review"}


def registry():
    result = StrategyRegistry()
    result.register(StrategyRegistration("review", ReviewStrategy(constraints=[], features=[]), ReviewState, initial_review))
    result.validate_schema(ReviewGraph)
    return result


def test_engine_registers_arbitrary_non_page_strategy_and_preserves_checkpoint():
    from gateway_helpers import GatewayHandle, RecordingChannel

    handle = GatewayHandle(RecordingChannel("review"))
    engine = ReviewEngine(model=Model(replies=[AIMessage(content="done")]), handle=handle.handle)
    config = {"configurable": {"thread_id": "review"}}
    first = engine.agent.invoke(
        {"messages": [HumanMessage(content="first")], "active_strategy": "review"}, config=config
    )
    second = engine.agent.invoke({"messages": [HumanMessage(content="second")]}, config=config)
    assert first["review"]["calls"] == 1
    assert second["review"]["calls"] == 2
    assert not {"general", "merchant", "probe"} & second.keys()
    engine.close()


def test_registries_and_initial_values_are_independent():
    one, two = registry(), registry()
    first, second = one.initial_state(), one.initial_state()
    first["review"]["notes"].append("private")
    assert second["review"]["notes"] == two.initial_state()["review"]["notes"] == []
    assert one.initial_state({"review": {"calls": 7, "notes": []}}) == {}


def test_registration_rejects_duplicate_reserved_and_missing_schema():
    instance = StrategyRegistry()
    registration = StrategyRegistration("review", ReviewStrategy(constraints=[], features=[]), ReviewState, initial_review)
    instance.register(registration)
    with pytest.raises(ValueError, match="Duplicate"):
        instance.register(registration)
    with pytest.raises(ValueError, match="reserved"):
        StrategyRegistry().register(
            StrategyRegistration("messages", ReviewStrategy(constraints=[], features=[]), ReviewState, initial_review)
        )
    with pytest.raises(TypeError, match="Root state"):
        instance.validate_schema(AgentState)
    instance.validate_schema(ReviewGraph)
    with pytest.raises(RuntimeError, match="frozen"):
        instance.register(registration)
    with pytest.raises(ValueError, match="Unregistered"):
        instance.get("merchant")
    with pytest.raises(ValueError, match="Incomplete"):
        instance.initial_state({"review": {"calls": 1}})
    with pytest.raises(ValueError):
        instance.initial_state({"review": {"calls": "broken", "notes": []}})


@pytest.mark.parametrize("asynchronous", [False, True])
def test_dispatch_uses_one_selection_and_propagates_handler_result(asynchronous):
    events = []

    class Recording(Strategy[ReviewGraph]):
        def wrap_tool_call(self, request, handler):
            events.append("before")
            result = handler(request)
            events.append("after")
            return result

        async def awrap_tool_call(self, request, handler):
            events.append("before")
            result = await handler(request)
            events.append("after")
            return result

    registered = StrategyRegistry()
    registered.register(StrategyRegistration("review", Recording(constraints=[], features=[]), ReviewState, initial_review))
    registered.validate_schema(ReviewGraph)

    dispatcher = StrategyDispatchMiddleware(state_schema=ReviewGraph, registry=registered)
    request = SimpleNamespace(state={"messages": [], "review": initial_review(), "active_strategy": "review"})
    result = Command(
        update={
            "review": {"calls": 4, "notes": []},
            "messages": [ToolMessage(content="ok", tool_call_id="one")],
        }
    )

    def handler(request):
        events.append("handler")
        request.state["review"]["calls"] = 9
        return result

    async def ahandler(request):
        return handler(request)

    actual = (
        asyncio.run(dispatcher.awrap_tool_call(request, ahandler))
        if asynchronous
        else dispatcher.wrap_tool_call(request, handler)
    )
    assert actual is result
    assert events == ["before", "handler", "after"]


def test_nested_update_preserves_evidence_and_independent_task_namespace():
    fields = initial_fields()
    visit = {"path": "app/shop", "visit_id": "v", "strategy": "merchant"}
    fields["general"]["visit"] = visit
    fields["merchant"] = begin_merchant_visit(visit)
    fields["merchant"]["evidence"]["latest_new_count"] = 8
    fields["probe"]["run_id"] = "task"
    update = complete_update(
        fields, {"merchant": {"phase": "traversing", "evidence": {"consecutive_empty_swipes": 0}}}
    )
    assert set(update) == {"merchant"}
    assert update["merchant"]["evidence"]["latest_new_count"] == 8
    assert merge_fields(fields, update)["probe"] == {"run_id": "task"}
    assert fields["merchant"]["phase"] == "awaiting_training"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_route_is_read_from_fixed_state_field(asynchronous):
    dispatcher = StrategyDispatchMiddleware(state_schema=ReviewGraph, registry=registry())
    current = {
        "messages": [],
        "review": initial_review(),
        "active_strategy": "review",
        "general": {"visit": {"strategy": "not-used"}},
    }
    result = (
        asyncio.run(dispatcher.abefore_model(current, None))
        if asynchronous
        else dispatcher.before_model(current, None)
    )
    assert result["review"]["calls"] == 1
    current["active_strategy"] = "missing"
    with pytest.raises(ValueError, match="Unregistered strategy"):
        if asynchronous:
            asyncio.run(dispatcher.abefore_model(current, None))
        else:
            dispatcher.before_model(current, None)
    del current["active_strategy"]
    with pytest.raises(KeyError, match="active_strategy"):
        dispatcher.before_model(current, None)


def test_route_update_is_preserved_with_page_state():
    fields = initial_fields()
    update = complete_update(fields, {"active_strategy": "merchant", "general": {"active": True}})
    merged = merge_fields(fields, update)
    assert update["active_strategy"] == merged["active_strategy"] == "merchant"
    assert fields["active_strategy"] == "general"
