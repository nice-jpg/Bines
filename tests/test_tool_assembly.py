"""Engine subclasses own the final tool list, including optional communication."""

from langchain_core.messages import AIMessage
from langchain_core.tools import StructuredTool
from src.agent_runtime.engine import AgentEngine
from tests.engine_helpers import Model, ScriptedEngine
from tests.tool_helpers import conductor_tools


def test_conductor_assembly_has_no_duplicate_or_legacy_tools():
    names = [tool.name for tool in conductor_tools()]
    assert len(names) == len(set(names))
    assert {"talk", "prepare_collector", "is_done", "collect_continuously", "get_probe_progress"} <= set(
        names
    )
    assert "authorize_probe_completion" not in names
    assert not {"notify_user", "query_manual", "tap", "swipe_back"} & set(names)


def test_base_engine_does_not_inject_talk_and_allows_custom_name():
    class NoCommunication(ScriptedEngine):
        def _create_tools(self):
            return []

    class CustomCommunication(ScriptedEngine):
        def _create_tools(self):
            def report(text: str) -> bool:
                """Report through the agent's channel."""
                return self.send_message(text)

            return [StructuredTool.from_function(report)]

    for cls in (NoCommunication, CustomCommunication):
        engine = cls(model=Model(replies=[AIMessage(content="done")]))
        try:
            assert "talk" not in [t.name for t in engine._create_tools()]
            assert callable(engine.send_message)
        finally:
            engine.close()


def test_custom_communication_tool_executes_through_engine_capability():
    from tests.gateway_helpers import GatewayHandle, RecordingChannel
    from src.agent_runtime.types.execution import EngineStatus

    class ReportingEngine(ScriptedEngine):
        def _create_tools(self):
            def report(text: str) -> bool:
                """Send a business report."""
                return self.send_message(text)

            return [StructuredTool.from_function(report)]

    channel = RecordingChannel()
    gateway = GatewayHandle(channel)
    model = Model(
        replies=[
            AIMessage(
                content="", tool_calls=[{"name": "report", "args": {"text": "custom message"}, "id": "r"}]
            ),
            AIMessage(content="done"),
        ]
    )
    engine = ReportingEngine(model=model, gateway_id=gateway.gateway_id)
    gateway.start()
    try:
        channel.receive("conversation", "start")
        assert engine.run_turn().status == EngineStatus.COMPLETED
        assert channel.output().text == "custom message"
    finally:
        engine.close()
        gateway.request_stop()
        gateway.finish_output()
        gateway.join()
