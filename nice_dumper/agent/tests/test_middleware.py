from types import SimpleNamespace

from nice_dumper_agent.middleware import (
    RecognizerCommunicationMiddleware,
    SubagentLifecycleMiddleware,
)
from nice_dumper_agent.models import FunctionRegion, RecognizerResult
from nice_dumper_agent.recognizer_agent import HiddenSubtreeCandidate


def test_recognizer_communication_middleware_records_call(monkeypatch) -> None:
    def fake_recognize_functions(model, xml_text):
        return RecognizerResult([FunctionRegion("[0,0][10,10]", "外卖")])

    monkeypatch.setattr("nice_dumper_agent.recognizer_agent.recognize_functions", fake_recognize_functions)
    middleware = RecognizerCommunicationMiddleware(model=object())

    result = middleware.call_recognizer("<hierarchy></hierarchy>")

    assert result.ok
    assert len(middleware.calls) == 1
    assert middleware.calls[0].function_count == 1


def test_subagent_middleware_keeps_hidden_analysis_in_model_context() -> None:
    runtime = SimpleNamespace(
        _subagent_roles={},
        recognition_store={},
        hidden_analysis_store={
            "XML0": [
                HiddenSubtreeCandidate(
                    path="0/1",
                    resource_id="pull_loading_bg_container",
                    bounds="[0,0][100,100]",
                    reason="inactive-preloaded-pull-layer",
                    descendant_count=57,
                    actionable_descendant_count=0,
                    overlapping_sibling_actionable_count=31,
                    estimated_characters=20250,
                    sample_labels=["最近使用"],
                )
            ]
        },
    )
    middleware = SubagentLifecycleMiddleware(runtime)

    update = middleware.before_model({"messages": [{"role": "user", "content": "continue"}]})

    content = update["messages"][0]["content"]
    assert "hidden_subtree_analysis" in content
    assert "pull_loading_bg_container" in content
    assert "20250" in content
