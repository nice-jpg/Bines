from __future__ import annotations

from types import SimpleNamespace

from langchain_core.messages import HumanMessage, SystemMessage

from mind_controller_agent.middleware import MasterTraceMiddleware
from mind_controller_agent.prompt_patch import PromptPatchError
from mind_controller_agent.tool_error import make_tool_error_message


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


def test_prompt_patch_error_metadata_is_returned_to_model() -> None:
    request = SimpleNamespace(
        tool_call={"name": "apply_patch", "id": "patch-1"},
        tool=SimpleNamespace(name="apply_patch"),
    )
    error = PromptPatchError(
        "invalid_change_line",
        "Hunk lines require a prefix.",
        line=4,
        path="system.md",
        hint="Prefix the replacement with '+'.",
    )

    message = make_tool_error_message(request, error)

    assert message.status == "error"
    assert message.tool_call_id == "patch-1"
    assert message.additional_kwargs == {
        "error_type": "PromptPatchError",
        "tool_name": "apply_patch",
        "error_code": "invalid_change_line",
        "line": 4,
        "path": "system.md",
    }
    assert "Prefix the replacement with '+'" in message.content
