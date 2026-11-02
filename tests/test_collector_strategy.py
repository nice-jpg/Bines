"""Collector strategy isolation through the production Engine graph."""

import asyncio
from unittest.mock import Mock

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage

from engine_helpers import Model
from gateway_helpers import GatewayHandle, RecordingChannel
from src.collector.engine import CollectorEngine
from src.collector.training import CollectorTrainingRegistry
from src.collector.state import CollectorAgentState
from src.collector.toolbox import CollectorToolbox
from src.strategy.collector.strategy import CollectorStrategy


def assignment():
    return {"page_path": "app/merchant", "page_visit_id": "visit", "run_id": "run"}


def make_engine():
    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    toolbox = Mock(spec=CollectorToolbox)
    toolbox.page_path = "app/merchant"
    toolbox.page_visit_id = "visit"
    toolbox.run_id = "run"
    toolbox.create_tools.return_value = []
    toolbox.mechanism = Mock()
    toolbox.mechanism.query_page_doc.return_value = "page description"
    model = Model(replies=[AIMessage(content="trained")])
    engine = CollectorEngine(
        model=model, toolbox=toolbox, session_id="collector-test",
        max_iterations=100, handle=gateway.handle, registry=CollectorTrainingRegistry(),
    )
    channel.receive("chat", "Train this page")
    return engine, model, channel


def test_collector_graph_registers_own_namespace_and_preserves_checkpoint():
    engine, model, channel = make_engine()
    try:
        assert engine.run_turn().output == "trained"
        assert engine._state["collector"] == assignment()
        assert "merchant" not in engine._state
        assert "general" not in engine._state
        channel.receive("chat", "check again")
        assert engine.run_turn().output == "trained"
        assert engine._state["collector"] == assignment()
        assert len(model.seen) == 2
    finally:
        engine.close()


@pytest.mark.parametrize("asynchronous", [False, True])
def test_collector_model_policy_preserves_settings_and_calls_handler_once(asynchronous):
    strategy = CollectorStrategy[CollectorAgentState](assignment())
    request = ModelRequest(
        model=Model(replies=[AIMessage(content="ok")]), messages=[],
        state={"messages": [], "collector": assignment()},
        model_settings={"temperature": 0.4, "parallel_tool_calls": True},
    )
    calls = []
    sentinel = object()

    def handler(updated):
        calls.append(updated)
        return sentinel

    async def async_handler(updated):
        return handler(updated)

    result = (
        asyncio.run(strategy.awrap_model_call(request, async_handler))
        if asynchronous else strategy.wrap_model_call(request, handler)
    )
    assert result is sentinel
    assert len(calls) == 1
    assert calls[0].model_settings == {"temperature": 0.4, "parallel_tool_calls": False}
    assert request.model_settings["parallel_tool_calls"] is True


@pytest.mark.parametrize("asynchronous", [False, True])
def test_collector_rejects_checkpoint_from_another_assignment(asynchronous):
    strategy = CollectorStrategy[CollectorAgentState](assignment())
    request = ModelRequest(
        model=Model(replies=[AIMessage(content="ok")]), messages=[],
        state={"messages": [], "collector": {**assignment(), "run_id": "other-run"}},
    )
    handler = Mock()
    with pytest.raises(ValueError, match="training assignment"):
        if asynchronous:
            asyncio.run(strategy.awrap_model_call(request, handler))
        else:
            strategy.wrap_model_call(request, handler)
    handler.assert_not_called()
