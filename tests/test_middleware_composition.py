from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.strategy.general.context.middleware import PageInputMiddleware
from src.agent_runtime.strategy.base import Strategy
from langchain.agents.middleware.types import AgentMiddleware


class _SyncLayer(AgentMiddleware):
    def __init__(self, name: str, events: list[str]) -> None:
        self.label = name
        self.events = events

    def wrap_tool_call(self, request, handler):
        self.events.append(f"{self.label}:before")
        result = handler(request)
        self.events.append(f"{self.label}:after")
        return result

    async def awrap_tool_call(self, request, handler):
        self.events.append(f"{self.label}:before")
        result = await handler(request)
        self.events.append(f"{self.label}:after")
        return result


class _ModelLayer:
    def __init__(self, name: str, events: list[str]) -> None:
        self.label = name
        self.events = events

    def wrap_model_call(self, request, handler):
        self.events.append(f"{self.label}:before")
        result = handler(request)
        self.events.append(f"{self.label}:after")
        return result

    async def awrap_model_call(self, request, handler):
        self.events.append(f"{self.label}:before")
        result = await handler(request)
        self.events.append(f"{self.label}:after")
        return result


class _RejectingLayer(_SyncLayer):
    def wrap_tool_call(self, request, handler):
        self.events.append(f"{self.label}:rejected")
        return "rejected"

    async def awrap_tool_call(self, request, handler):
        return self.wrap_tool_call(request, handler)


def test_tool_execution_policy_has_one_deterministic_execution_order() -> None:
    events: list[str] = []
    middleware = Strategy(constraints=[_SyncLayer("dangerous", events)], features=[_SyncLayer("notice", events)])
    middleware._execute = lambda request, handler: handler(request)

    result = middleware.wrap_tool_call("request", lambda _request: events.append("tool") or "ok")

    assert result == "ok"
    assert events == [
        "dangerous:before",
        "notice:before",
        "tool",
        "notice:after",
        "dangerous:after",
    ]


def test_dangerous_rejection_does_not_notify_or_execute() -> None:
    events: list[str] = []
    middleware = Strategy(constraints=[_RejectingLayer("dangerous", events)], features=[_SyncLayer("notice", events)])

    result = middleware.wrap_tool_call("request", lambda _request: events.append("tool"))

    assert result == "rejected"
    assert events == [
        "dangerous:rejected",
    ]



def test_conductor_context_projects_before_adding_transient_manual_context() -> None:
    events: list[str] = []
    middleware = object.__new__(PageInputMiddleware)
    middleware._compression = _ModelLayer("compression", events)
    middleware._manual = _ModelLayer("manual", events)

    result = middleware.wrap_model_call("request", lambda _request: events.append("model") or "ok")

    assert result == "ok"
    assert events == [
        "compression:before",
        "model",
        "compression:after",
    ]


def test_tool_execution_policy_async_order_matches_sync() -> None:
    events: list[str] = []
    middleware = Strategy(constraints=[_SyncLayer("dangerous", events)], features=[_SyncLayer("notice", events)])

    async def execute(request, handler):
        return await handler(request)

    middleware._aexecute = execute

    async def handler(_request):
        events.append("tool")
        return "ok"

    result = asyncio.run(middleware.awrap_tool_call("request", handler))

    assert result == "ok"
    assert events == [
        "dangerous:before",
        "notice:before",
        "tool",
        "notice:after",
        "dangerous:after",
    ]


def test_async_admission_rejection_skips_all_features():
    events = []
    strategy = Strategy(
        constraints=[_RejectingLayer("admission", events)],
        features=[_SyncLayer("snapshot", events), _SyncLayer("collection", events)],
    )

    async def execute(request):
        raise AssertionError("rejected tool executed")

    assert asyncio.run(strategy.awrap_tool_call(None, execute)) == "rejected"
    assert events == ["admission:rejected"]


def test_model_hook_updates_are_visible_without_mutating_history():
    from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage

    class Remove(AgentMiddleware):
        def after_model(self, state, runtime):
            return {"messages": [RemoveMessage(id="old")]}

    class Observe(AgentMiddleware):
        def after_model(self, state, runtime):
            assert state["messages"] == []
            return {"messages": [SystemMessage(content="continue", id="new")], "jump_to": "model"}

    class Unreachable(AgentMiddleware):
        def after_model(self, state, runtime):
            raise AssertionError("jump must short-circuit remaining hooks")

    state = {"messages": [AIMessage(content="unfinished", id="old")]}
    strategy = Strategy(constraints=[Remove(), Observe()], features=[Unreachable()])
    result = strategy.after_model(state, None)
    assert result["jump_to"] == "model"
    assert len(result["messages"]) == 2
    assert state["messages"][0].id == "old"
