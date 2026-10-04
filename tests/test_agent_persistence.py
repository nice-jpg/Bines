from __future__ import annotations

import json
import itertools
from types import SimpleNamespace

from src.agent import AgentRunResult, AgentRuntime
from src.agent_runtime import AgentPersistenceLayer, AgentResponse


def _result(
    content: str,
    *,
    complete: bool = False,
    stopped_by: str = "create_agent",
    interrupted: bool = False,
) -> AgentRunResult:
    return AgentRunResult(
        output=content,
        state={},
        stopped_by=stopped_by,
        interrupted=interrupted,
        completion_allowed=complete,
    )


def _completed(summary: str = "done") -> AgentRunResult:
    return _result(
        json.dumps(
            {
                "resolved": True,
                "summary": summary,
                "result": {"items": 3},
                "next_action": None,
            }
        ),
        complete=True,
        stopped_by="completed",
    )


class FakeRuntime:
    def __init__(self, initial: AgentRunResult, continuations: list[AgentRunResult]) -> None:
        self.initial = initial
        self.continuations = list(continuations)
        self.pending_kind: str | None = None
        self.calls: list[tuple] = []

    def run_turn(self, messages, *, session_id, max_iterations):
        self.calls.append(("run", list(messages), session_id, max_iterations))
        return self.initial

    def _continue_turn(self, *, session_id, reason, max_iterations):
        self.calls.append(("continue", session_id, reason, max_iterations))
        return self.continuations.pop(0)

    def resume_turn(self, *, session_id, user_input=None, max_iterations):
        self.calls.append(("resume", session_id, user_input, max_iterations))
        self.pending_kind = None
        return self.continuations.pop(0)

    def has_pending_interrupt(self, _session_id):
        return self.pending_kind is not None

    def pending_interrupt_kind(self, _session_id):
        return self.pending_kind


def test_unresolved_response_continues_same_session_until_structured_completion() -> None:
    unresolved = _result(
        json.dumps(
            {
                "resolved": False,
                "summary": "more work remains",
                "result": {},
                "next_action": "swipe the active merchant page",
            }
        )
    )
    runtime = FakeRuntime(unresolved, [_completed("probe complete")])
    persistence = AgentPersistenceLayer(runtime)

    result = persistence.run_turn(
        [{"role": "user", "content": "probe"}],
        session_id="thread-1",
        max_iterations=40,
    )

    assert [call[0] for call in runtime.calls] == ["run", "continue"]
    assert runtime.calls[1][1] == "thread-1"
    assert runtime.calls[1][2] == "agent_reported_unresolved"
    assert result.stopped_by == "completed"
    assert result.structured_response == {
        "resolved": True,
        "summary": "probe complete",
        "result": {"items": 3},
        "next_action": None,
    }


def test_claimed_completion_is_rejected_when_runtime_has_not_approved() -> None:
    claimed = _completed()
    claimed = AgentRunResult(
        output=claimed.output,
        state={},
        stopped_by="create_agent",
        completion_allowed=False,
    )
    runtime = FakeRuntime(claimed, [_completed()])

    result = AgentPersistenceLayer(runtime).run_turn([], session_id="thread-2")

    assert runtime.calls[1][2] == "task_state_is_not_complete"
    assert result.structured_response is not None
    assert result.structured_response["resolved"] is True


def test_malformed_completion_response_is_repaired_in_same_context() -> None:
    runtime = FakeRuntime(
        _result("task finished", complete=True, stopped_by="completed"),
        [_completed()],
    )

    result = AgentPersistenceLayer(runtime).run_turn([], session_id="thread-3")

    assert runtime.calls[1][2] == "structured_response_required"
    assert AgentResponse.parse(result.output) is not None


def test_structured_response_rejects_extra_fields_and_invalid_next_action() -> None:
    assert AgentResponse.parse(
        '{"resolved": true, "summary": "done", "result": {}, "next_action": null, "extra": 1}'
    ) is None
    assert AgentResponse.parse(
        '{"resolved": false, "summary": "pending", "result": {}, "next_action": null}'
    ) is None


def test_transport_and_recursion_interrupts_resume_automatically() -> None:
    for kind in ("model_transport", "recursion", "liveness"):
        interrupted = _result("paused", interrupted=True, stopped_by=f"{kind}_interrupt")
        runtime = FakeRuntime(interrupted, [_completed(kind)])
        runtime.pending_kind = kind

        result = AgentPersistenceLayer(runtime).run_turn([], session_id=f"thread-{kind}")

        assert [call[0] for call in runtime.calls] == ["run", "resume"]
        assert result.structured_response["summary"] == kind


def test_human_interrupt_is_not_resumed_without_user_input() -> None:
    interrupted = _result("captcha required", interrupted=True, stopped_by="human_interrupt")
    runtime = FakeRuntime(interrupted, [])
    runtime.pending_kind = "human"

    result = AgentPersistenceLayer(runtime).run_turn([], session_id="thread-human")

    assert result is interrupted
    assert [call[0] for call in runtime.calls] == ["run"]


def test_agent_runtime_continuation_appends_only_internal_instruction_to_same_thread() -> None:
    class CapturingAgent:
        def __init__(self) -> None:
            self.payload = None
            self.config = None
            self._ntrace = SimpleNamespace(update=lambda **_kwargs: None)

        def invoke(self, payload, config):
            self.payload = payload
            self.config = config
            return {
                "messages": [
                    *payload["messages"],
                    {"role": "assistant", "content": "continued"},
                ]
            }

    agent = CapturingAgent()
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.model = "fake"
    runtime.tools = []
    runtime.name = "test"
    runtime.agent = agent
    runtime._trace = SimpleNamespace(update=lambda **_kwargs: None)
    runtime.sessions = {"thread-context": [{"role": "user", "content": "original"}]}
    runtime._stateless_counter = itertools.count(1)
    runtime._pending_interrupts = {}
    from threading import RLock
    runtime._communications = {}
    runtime._communication_lock = RLock()
    runtime._closing = False

    result = runtime._continue_turn(
        session_id="thread-context",
        reason="work_remaining",
        max_iterations=17,
    )

    assert len(agent.payload["messages"]) == 1
    assert agent.payload["messages"][0]["role"] == "system"
    assert "work_remaining" in agent.payload["messages"][0]["content"]
    assert "original" not in str(agent.payload)
    assert agent.config["configurable"]["thread_id"] == "thread-context"
    assert agent.config["recursion_limit"] == 17
    assert result.output == "continued"
