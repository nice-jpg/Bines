from contextvars import ContextVar

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import interrupt
from langchain_core.tools import StructuredTool

from src.agent_runtime.engine import AgentEngine
from src.agent_runtime.contracts import TaskResult
from tests.engine_helpers import ScriptedEngine, Model
from trainer.raw_run_adapter import PAGE_MECHANISM_DIR, RawRunSlave, TrainingEngine


class ReportEngine(ScriptedEngine):
    def _evaluate_result(self, state):
        result = super()._evaluate_result(state)
        for task_id in self.handle.mailbox.unfinished():
            self.report(task_id, TaskResult(status="completed", summary=result.output))
        return result
    @property
    def evaluation_state(self):
        return dict(self._state)


def slave_for(model, **kwargs):
    return RawRunSlave(
        engine_factory=lambda **options: ReportEngine(**options),
        model_factory=lambda: model,
        notifier_factory=lambda: lambda text: None,
        print_result=False,
        **kwargs,
    )


def test_adapter_runs_graph_and_evaluates_report():
    evaluated = []
    slave = slave_for(
        Model(replies=[AIMessage(content="done")]), evaluator=lambda r: evaluated.append(r) or 73
    )
    result = slave.run()
    assert result.output == "done"
    assert slave.eval(result) == 73
    assert evaluated == [result]
    assert result.summary[0]["type"] == "human"


def test_debug_info_declares_existing_prompt_files():
    info = RawRunSlave().debug_info()
    assert all(path.is_file() for path in info.prompt_paths)
    assert {p for p in info.prompt_paths if p.name == "PAGE.md"} == set(PAGE_MECHANISM_DIR.rglob("PAGE.md"))


def test_adapter_handles_repeated_human_interrupts_and_detaches_context():
    marker = ContextVar("parent", default=None)
    observed = []

    def captcha() -> str:
        """Wait for verification."""
        observed.append(marker.get())
        return interrupt({"kind": "human", "message": "captcha"})

    tool = StructuredTool.from_function(captcha)
    model = Model(
        replies=[
            AIMessage(
                content="", tool_calls=[{"name": "captcha", "args": {}, "id": "a", "type": "tool_call"}]
            ),
            AIMessage(
                content="", tool_calls=[{"name": "captcha", "args": {}, "id": "b", "type": "tool_call"}]
            ),
            AIMessage(content="complete"),
        ]
    )
    inputs = iter(["not yet", "done", "done"])
    slave = RawRunSlave(
        engine_factory=lambda **kw: ReportEngine(tools=[tool], **kw),
        model_factory=lambda: model,
        notifier_factory=lambda: lambda _: None,
        human_input_provider=lambda _: next(inputs),
        print_result=False,
    )
    token = marker.set("parent-graph")
    try:
        report = slave.run()
    finally:
        marker.reset(token)
    assert report.output == "complete"
    assert observed == [None] * 4


def test_report_summary_omits_tool_payloads():
    messages = [
        HumanMessage(content="start"),
        AIMessage(content="inspect", tool_calls=[{"name": "uiautomate", "args": {}, "id": "ui"}]),
        ToolMessage(content="large XML", name="uiautomate", tool_call_id="ui"),
        ToolMessage(content="notice", name="notify_user", tool_call_id="notice"),
    ]
    from trainer.raw_run_adapter import RunResult

    report = RunResult("done", {"messages": messages})
    summary = RawRunSlave()._summary(report)
    assert len(summary) == 3
    assert summary[-1] == {"type": "tool", "name": "uiautomate", "status": "success"}
    assert "large XML" not in str(summary)


def test_construction_failure_releases_gateway():
    def fail(**kwargs):
        raise ValueError("construction")

    slave = RawRunSlave(engine_factory=fail, model_factory=lambda: "fake")
    with pytest.raises(ValueError, match="construction"):
        slave.run()


def test_training_capabilities_belong_to_engine():
    assert issubclass(TrainingEngine, AgentEngine)
    assert TrainingEngine._create_tools.__qualname__ == "ConductorEngine._create_tools"
