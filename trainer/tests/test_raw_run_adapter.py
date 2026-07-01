from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import tool_call

from trainer.raw_run_adapter import APP_PROBE_MESSAGE, RawRunSlave


@dataclass
class FakeResult:
    output: str
    interrupted: bool = False
    state: dict[str, Any] = field(default_factory=lambda: {"messages": []})
    summary: list[Any] | None = None


class FakeRuntime:
    init_kwargs: dict[str, Any] | None = None
    run_kwargs: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_kwargs = kwargs

    def run_turn(self, messages, **kwargs: Any) -> FakeResult:
        type(self).run_kwargs = {"messages": messages, **kwargs}
        return FakeResult(output="done")


class InterruptingRuntime(FakeRuntime):
    resume_calls: list[dict[str, Any]] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.pending = False
        self.resume_count = 0

    def run_turn(self, messages, **kwargs: Any) -> FakeResult:
        type(self).run_kwargs = {"messages": messages, **kwargs}
        self.pending = True
        return FakeResult(output="", interrupted=True)

    def has_pending_interrupt(self, session_id: str) -> bool:
        return self.pending

    def resume_turn(self, **kwargs: Any) -> FakeResult:
        type(self).resume_calls.append(kwargs)
        self.resume_count += 1
        if self.resume_count < 2:
            return FakeResult(output="", interrupted=True)
        self.pending = False
        return FakeResult(output="complete")


def test_adapter_runs_raw_workflow_and_returns_exact_result() -> None:
    evaluated: list[FakeResult] = []

    def evaluate(result: FakeResult) -> int:
        evaluated.append(result)
        return 73

    slave = RawRunSlave(
        evaluator=evaluate,
        runtime_factory=FakeRuntime,
        model_factory=lambda: "model",
        tool_factory=lambda notifier: ["tool"],
        notifier_factory=lambda: lambda text: None,
        print_result=False,
    )

    result = slave.run()
    score = slave.eval(result)

    assert result.output == "done"
    assert result.summary == []
    assert evaluated[0] is result
    assert score == 73
    assert FakeRuntime.init_kwargs == {
        "model": "model",
        "tools": ["tool"],
        "name": "main",
    }
    assert FakeRuntime.run_kwargs == {
        "messages": [{"role": "user", "content": APP_PROBE_MESSAGE}],
        "session_id": "main",
        "max_iterations": 1000,
    }


def test_debug_info_declares_existing_prompt_files() -> None:
    slave = RawRunSlave(print_result=False)
    info = slave.debug_info()

    assert info.prompt_paths
    assert all(path.is_file() for path in info.prompt_paths)
    assert any(path.name == "system_prompt.py" for path in info.prompt_paths)
    assert sum(path.name == "PAGE.md" for path in info.prompt_paths) == 5
    assert info.working_directory is not None


def test_adapter_resumes_repeated_hitl_interrupts_before_returning() -> None:
    InterruptingRuntime.resume_calls = []
    inputs = iter(["not-yet", "done", "done"])
    evaluated: list[FakeResult] = []

    def evaluate(result: FakeResult) -> int:
        evaluated.append(result)
        return 90

    slave = RawRunSlave(
        evaluator=evaluate,
        runtime_factory=InterruptingRuntime,
        model_factory=lambda: "model",
        tool_factory=lambda notifier: ["tool"],
        notifier_factory=lambda: lambda text: None,
        human_input_provider=lambda result: next(inputs),
        print_result=False,
    )

    result = slave.run()
    score = slave.eval(result)

    assert result.output == "complete"
    assert result.summary == []
    assert evaluated == [result]
    assert score == 90
    assert InterruptingRuntime.resume_calls == [
        {"session_id": "main", "user_input": "done", "max_iterations": 1000},
        {"session_id": "main", "user_input": "done", "max_iterations": 1000},
    ]


def test_adapter_rejects_interrupted_result_without_pending_checkpoint() -> None:
    class BrokenRuntime(FakeRuntime):
        def run_turn(self, messages, **kwargs: Any) -> FakeResult:
            return FakeResult(output="", interrupted=True)

        def has_pending_interrupt(self, session_id: str) -> bool:
            return False

    slave = RawRunSlave(
        runtime_factory=BrokenRuntime,
        model_factory=lambda: "model",
        tool_factory=lambda notifier: ["tool"],
        notifier_factory=lambda: lambda text: None,
        human_input_provider=lambda result: "done",
        print_result=False,
    )

    with pytest.raises(RuntimeError, match="without a pending runtime checkpoint"):
        slave.run()


def test_adapter_builds_compact_summary_without_tool_output_content() -> None:
    class SummaryRuntime(FakeRuntime):
        def run_turn(self, messages, **kwargs: Any) -> FakeResult:
            return FakeResult(
                output="full final output",
                state={
                    "messages": [
                        HumanMessage(content="start"),
                        AIMessage(
                            content="inspect",
                            tool_calls=[
                                tool_call(
                                    name="uiautomate",
                                    args={"action": "dump"},
                                    id="ui-1",
                                )
                            ],
                        ),
                        ToolMessage(
                            content="large XML that must not reach master",
                            name="uiautomate",
                            tool_call_id="ui-1",
                        ),
                        ToolMessage(
                            content="notice",
                            name="notify_user",
                            tool_call_id="notice-1",
                        ),
                    ]
                },
            )

    slave = RawRunSlave(
        runtime_factory=SummaryRuntime,
        model_factory=lambda: "model",
        tool_factory=lambda notifier: ["tool"],
        notifier_factory=lambda: lambda text: None,
        print_result=False,
    )

    result = slave.run()

    assert result.output == "full final output"
    assert result.state["messages"][2].content == "large XML that must not reach master"
    assert result.summary == [
        {"type": "human", "content": "start"},
        {
            "type": "ai",
            "content": "inspect",
            "tool_calls": [{"name": "uiautomate", "args": {"action": "dump"}}],
        },
        {"type": "tool", "name": "uiautomate", "status": "success"},
    ]
