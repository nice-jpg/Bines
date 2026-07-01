from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from mind_controller_agent.middleware import MasterTraceMiddleware


class FakeRequest:
    def __init__(self, messages):
        self.messages = list(messages)

    def override(self, *, messages):
        return FakeRequest(messages)


def test_trace_is_appended_only_to_model_request() -> None:
    controller = SimpleNamespace(
        rounds=[],
        changes=[],
        best_score=None,
        best_round=0,
    )
    middleware = MasterTraceMiddleware(controller)
    original_message = HumanMessage(content="existing history")
    request = FakeRequest([original_message])
    captured = {}

    def handler(updated_request):
        captured["request"] = updated_request
        return "response"

    response = middleware.wrap_model_call(request, handler)

    assert response == "response"
    assert request.messages == [original_message]
    assert captured["request"].messages[0] is original_message
    trace = captured["request"].messages[-1]
    assert isinstance(trace, SystemMessage)
    assert trace.content.startswith("Authoritative cognitive-control state:")
