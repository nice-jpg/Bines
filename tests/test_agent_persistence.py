"""Coordinator execution policy and conductor response validation."""

import json
from unittest.mock import Mock
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from src.agent_runtime.coordinator import AgentCoordinator
from src.agent_runtime.types.execution import EngineResult, EngineStatus, InterruptKind
from src.agent_runtime.types.response import AgentResponse
from engine_helpers import Model, ScriptedEngine


def completed(summary="done"):
    return json.dumps({"resolved": True, "summary": summary, "result": {}, "next_action": None})


@pytest.mark.parametrize(
    "initial",
    [
        "not JSON",
        json.dumps({"resolved": False, "summary": "working", "result": {}, "next_action": "continue"}),
    ],
)
def test_unresolved_response_continues_same_checkpoint(initial):
    engine = ScriptedEngine(
        model=Model(replies=[AIMessage(content=initial), AIMessage(content=completed())]),
        structured=True,
        initial_messages=[HumanMessage(content="go")],
    )
    result = AgentCoordinator(engine).run()
    assert result.status == EngineStatus.COMPLETED
    assert AgentResponse.parse(result.output).resolved
    assert sum(isinstance(m, HumanMessage) for m in engine._state["messages"]) == 1
    assert any("agent_persistence_continuation" in str(m.content) for m in engine._state["messages"])


@pytest.mark.parametrize(
    "kind", [InterruptKind.LIVENESS, InterruptKind.MODEL_TRANSPORT, InterruptKind.RECURSION]
)
def test_coordinator_automatically_resumes_selected_interrupts(kind):
    engine = Mock(gateway_id="test", conversation=Mock(route={}))
    engine.run_turn.return_value = EngineResult(EngineStatus.INTERRUPTED, interrupt_kind=kind)
    engine.resume_turn.return_value = EngineResult(EngineStatus.COMPLETED, "done")
    assert AgentCoordinator(engine).run().output == "done"
    engine.resume_turn.assert_called_once_with()
    engine.continue_turn.assert_not_called()


@pytest.mark.parametrize("kind", [InterruptKind.HUMAN, InterruptKind.EXECUTION])
def test_coordinator_returns_human_or_permanent_interrupt_without_resuming(kind):
    engine = Mock(gateway_id="test", conversation=Mock(route={}))
    engine.run_turn.return_value = EngineResult(EngineStatus.INTERRUPTED, interrupt_kind=kind)
    assert AgentCoordinator(engine).run().status == EngineStatus.INTERRUPTED
    engine.resume_turn.assert_not_called()


def test_coordinator_resumes_prevalidated_input():
    engine = Mock(gateway_id="test", conversation=Mock(route={}))
    engine.run_turn.return_value = EngineResult(EngineStatus.RESUME_READY)
    engine.resume_turn.return_value = EngineResult(EngineStatus.COMPLETED, "done")
    AgentCoordinator(engine).run()
    engine.resume_turn.assert_called_once_with()


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        "{}",
        '{"resolved": true}',
        json.dumps({"resolved": True, "summary": "", "result": {}, "next_action": None}),
        json.dumps({"resolved": False, "summary": "working", "result": {}, "next_action": None}),
    ],
)
def test_response_parser_rejects_invalid_completion(payload):
    assert AgentResponse.parse(payload) is None


def test_structured_completion_is_not_vetoed_by_business_gate():
    class GatedEngine(ScriptedEngine):
        def _completion_allowed(self, state):
            return len(self.model_for_test.seen) > 1

    model = Model(replies=[AIMessage(content=completed())])
    engine = GatedEngine(model=model, structured=True, initial_messages=[HumanMessage(content="go")])
    engine.model_for_test = model
    assert AgentCoordinator(engine).run().status == EngineStatus.COMPLETED
    assert len(model.seen) == 1
