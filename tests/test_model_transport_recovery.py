from __future__ import annotations

import itertools
from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from websockets.exceptions import ConnectionClosedError

from src.agent import AgentRuntime


class RecoverableFakeAgent:
    def __init__(self) -> None:
        self.invocations: list[object] = []
        self.configs: list[object] = []
        self.should_fail = True
        self.snapshot = SimpleNamespace(
            values={"messages": [HumanMessage(content="preserved")]}
        )

    def invoke(self, value, config):
        self.invocations.append(value)
        self.configs.append(config)
        if self.should_fail:
            raise ConnectionClosedError(None, None, None)
        return {
            "messages": [
                HumanMessage(content="preserved"),
                {"role": "assistant", "content": "resumed"},
            ]
        }

    def get_state(self, config):
        self.configs.append(config)
        return self.snapshot


def make_runtime(agent: RecoverableFakeAgent) -> AgentRuntime:
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.model = "fake"
    runtime.tools = []
    runtime.name = "test"
    runtime.agent = agent
    runtime.sessions = {}
    runtime._stateless_counter = itertools.count(1)
    runtime._pending_interrupts = {}
    return runtime


def test_transport_failure_with_session_can_resume_failed_model_node() -> None:
    agent = RecoverableFakeAgent()
    runtime = make_runtime(agent)

    interrupted = runtime.run_turn(
        [{"role": "user", "content": "probe"}],
        session_id="session-1",
    )

    assert interrupted.interrupted is True
    assert interrupted.stopped_by == "model_transport_interrupt"
    assert runtime.pending_interrupt_kind("session-1") == "model_transport"
    assert interrupted.state == agent.snapshot.values

    agent.should_fail = False
    resumed = runtime.resume_turn(session_id="session-1", user_input="继续")

    assert agent.invocations == [
        {"messages": [{"role": "user", "content": "probe"}]},
        None,
    ]
    assert resumed.output == "resumed"
    assert runtime.has_pending_interrupt("session-1") is False


def test_transport_failure_without_session_is_not_claimed_recoverable() -> None:
    runtime = make_runtime(RecoverableFakeAgent())

    result = runtime.run_turn([{"role": "user", "content": "probe"}])

    assert result.interrupted is False
    assert result.stopped_by == "model_transport_error"
    assert result.interrupts == [
        {
            "type": "model_transport",
            "error_type": "ConnectionClosedError",
            "retryable": False,
        }
    ]
