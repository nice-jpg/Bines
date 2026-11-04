import json
import os
import subprocess
import sys

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from src.agent_runtime.coordinator import AgentCoordinator
from src.agent_runtime.types.execution import EngineStatus
from engine_helpers import ScriptedEngine, run_engine


class Model(FakeMessagesListChatModel):
    def bind_tools(self, *args, **kwargs):
        return self


def test_runtime_uses_checkpoint_history_without_resubmitting_messages(monkeypatch):
    monkeypatch.setenv("NTRACE_ENABLED", "0")
    runtime = ScriptedEngine(
        name="test",
        model=Model(responses=[AIMessage(content="one"), AIMessage(content="two")]),
        tools=[],
        system_prompt="test",
    )
    run_engine(runtime, [HumanMessage(content="first")], session_id="same")
    result = run_engine(runtime, [HumanMessage(content="second")], session_id="same")
    assert [message.content for message in runtime._state["messages"]] == ["first", "one", "second", "two"]
    other = run_engine(runtime, [HumanMessage(content="separate")], session_id="other")
    assert len(runtime._state["messages"]) == 2


def test_generic_tool_failure_is_returned_without_replaying_action(monkeypatch):
    monkeypatch.setenv("NTRACE_ENABLED", "0")
    calls = []

    @tool
    def fail() -> str:
        """Fail once to exercise the runtime boundary."""
        calls.append("attempt")
        raise ValueError("invalid operation")

    runtime = ScriptedEngine(
        name="test",
        model=Model(
            responses=[
                AIMessage(content="", tool_calls=[{"id": "action", "name": "fail", "args": {}}]),
                AIMessage(content="handled"),
            ]
        ),
        tools=[fail],
        system_prompt="test",
    )
    result = run_engine(runtime, [HumanMessage(content="go")], session_id="errors")
    error = next(message for message in runtime._state["messages"] if isinstance(message, ToolMessage))
    assert error.status == "error"
    assert calls == ["attempt"]
    assert result.output == "handled"


def test_persistence_restarts_toolless_output_on_same_graph(monkeypatch):
    monkeypatch.setenv("NTRACE_ENABLED", "0")
    completion = {"resolved": True, "summary": "done", "result": {}, "next_action": None}
    runtime = ScriptedEngine(
        name="test",
        model=Model(
            responses=[
                AIMessage(content="still working"),
                AIMessage(content=json.dumps(completion)),
            ]
        ),
        tools=[],
        system_prompt="test",
    )
    runtime._structured = True
    runtime.initial_messages = [HumanMessage(content="go")]
    result = AgentCoordinator(runtime).run()
    assert json.loads(result.output) == completion
    assert sum(isinstance(message, HumanMessage) for message in runtime._state["messages"]) == 1


def test_agent_and_trace_name_are_explicit_not_environment_owned(monkeypatch):
    monkeypatch.setenv("NTRACE_AGENT_NAME", "configured-name")
    monkeypatch.setenv("NTRACE_ENABLED", "0")
    runtime = ScriptedEngine(
        name="explicit-name", model=Model(responses=[AIMessage(content="ok")]), tools=[], system_prompt="test"
    )
    assert runtime._trace.agent_name == "explicit-name"
    assert runtime.agent.name == "explicit-name"
    assert not hasattr(runtime.agent, "_ntrace")


def test_runtime_import_has_no_device_or_business_middleware_dependency():
    code = "import sys; import src.agent_runtime; assert 'src.conductor.engine' not in sys.modules; assert 'src.strategy.general.page_visit' not in sys.modules; assert 'src.providers.codex' not in sys.modules; assert 'Reco' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "NTRACE_ENABLED": "0"})


def test_resume_payload_is_not_interpreted_as_an_application_protocol(monkeypatch):
    monkeypatch.setenv("NTRACE_ENABLED", "0")
    runtime = ScriptedEngine(
        name="test", model=Model(responses=[AIMessage(content="ok")]), tools=[], system_prompt="test"
    )
    payload = {"approval": "approved", "data": [1, 2]}
    assert runtime._resume_payload("human", payload) is payload
    assert runtime._resume_payload("human", None) is None


def test_runtime_import_does_not_load_application_assembly():
    from pathlib import Path

    source = str(Path(__file__).resolve().parents[1])
    code = "import sys; import src.agent_runtime; assert 'src.conductor.engine' not in sys.modules; assert 'src.tool_assembly' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True, env={**os.environ, "PYTHONPATH": source})
