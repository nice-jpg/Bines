from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trainer.raw_run_adapter import APP_PROBE_MESSAGE, RawRunSlave


@dataclass
class FakeResult:
    output: str


class FakeRuntime:
    init_kwargs: dict[str, Any] | None = None
    run_kwargs: dict[str, Any] | None = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).init_kwargs = kwargs

    def run_turn(self, messages, **kwargs: Any) -> FakeResult:
        type(self).run_kwargs = {"messages": messages, **kwargs}
        return FakeResult(output="done")


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

