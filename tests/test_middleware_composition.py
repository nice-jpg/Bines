from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.strategy.general.context.middleware import PageInputMiddleware
from src.middleware.execution.policy import ToolExecutionPolicyMiddleware


class _SyncLayer:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    def wrap_tool_call(self, request, handler):
        self.events.append(f"{self.name}:before")
        result = handler(request)
        self.events.append(f"{self.name}:after")
        return result

    async def awrap_tool_call(self, request, handler):
        self.events.append(f"{self.name}:before")
        result = await handler(request)
        self.events.append(f"{self.name}:after")
        return result


class _ModelLayer:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    def wrap_model_call(self, request, handler):
        self.events.append(f"{self.name}:before")
        result = handler(request)
        self.events.append(f"{self.name}:after")
        return result

    async def awrap_model_call(self, request, handler):
        self.events.append(f"{self.name}:before")
        result = await handler(request)
        self.events.append(f"{self.name}:after")
        return result


class _RejectingLayer(_SyncLayer):
    def wrap_tool_call(self, request, handler):
        self.events.append(f"{self.name}:rejected")
        return "rejected"


def test_tool_execution_policy_has_one_deterministic_execution_order() -> None:
    events: list[str] = []
    middleware = object.__new__(ToolExecutionPolicyMiddleware)
    middleware._dangerous = _SyncLayer("dangerous", events)
    middleware._notice = _SyncLayer("notice", events)
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
    middleware = object.__new__(ToolExecutionPolicyMiddleware)
    middleware._dangerous = _SyncLayer("dangerous", events)
    middleware._dangerous = _RejectingLayer("dangerous", events)
    middleware._notice = _SyncLayer("notice", events)
    middleware._execute = lambda request, handler: handler(request)

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
        "manual:before",
        "model",
        "manual:after",
        "compression:after",
    ]


def test_tool_execution_policy_async_order_matches_sync() -> None:
    events: list[str] = []
    middleware = object.__new__(ToolExecutionPolicyMiddleware)
    middleware._dangerous = _SyncLayer("dangerous", events)
    middleware._notice = _SyncLayer("notice", events)

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
