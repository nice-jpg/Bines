from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from middleware.page_context import (
    PAGE_CONTEXT_PATH,
    PAGE_CONTEXT_PENDING_PATH,
    PAGE_VISIT_ID,
)
from agent import AgentRuntime
from middleware.probe_lifecycle import ProbeLifecycleMiddleware
from middleware.probe_state import (
    MERCHANT_COLLECTION_DONE,
    PROBE_COMPLETED,
    PROBE_COMPLETION_AUTHORIZED,
    PROBE_PREMATURE_FINAL_COUNT,
    PROBE_RUNNING,
    PROBE_STATUS,
    PROBE_TARGET_LEAF_PATHS,
    PROBE_VISITED_LEAF_PATHS,
)
from page_mechanism import PageMechanism


def _mechanism(tmp_path: Path) -> PageMechanism:
    for relative in ("meituan/外卖/美食", "meituan/外卖/美食/商家"):
        page = tmp_path / relative
        page.mkdir(parents=True)
        (page / "PAGE.md").write_text("# Page\n", encoding="utf-8")
        (page / "task.md").write_text("# Task\n", encoding="utf-8")
    return PageMechanism(tmp_path)


def _state(*, message: AIMessage | None = None, path: str = "meituan/外卖/美食") -> dict:
    return {
        PROBE_STATUS: PROBE_RUNNING,
        PROBE_PREMATURE_FINAL_COUNT: 0,
        PROBE_TARGET_LEAF_PATHS: ("meituan/外卖/美食",),
        PROBE_VISITED_LEAF_PATHS: (),
        PAGE_CONTEXT_PATH: path,
        PAGE_CONTEXT_PENDING_PATH: "",
        PAGE_VISIT_ID: "visit-1",
        "messages": [message or AIMessage(content="任务还需继续", id="ai-1")],
    }


def _request(tool_name: str, state: dict, **args) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool_name, "id": f"{tool_name}-1", "args": args},
        tool=None,
        state=state,
    )


def _tool_message(name: str) -> ToolMessage:
    return ToolMessage(content="ok", name=name, tool_call_id=f"{name}-1")


def _payload(command_or_message) -> dict:
    message = (
        command_or_message.update["messages"][-1]
        if isinstance(command_or_message, Command)
        else command_or_message
    )
    return json.loads(message.content)


def test_premature_final_is_removed_and_jumps_back_to_model(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))

    update = middleware.after_model(_state(), runtime=None)

    assert update["jump_to"] == "model"
    assert update[PROBE_PREMATURE_FINAL_COUNT] == 1
    assert isinstance(update["messages"][0], RemoveMessage)
    assert "configured_leaf_pages_unvisited" in update["messages"][1].content


def test_tool_call_resets_consecutive_premature_final_count(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state(message=AIMessage(content="", tool_calls=[{"name": "uiautomate", "args": {}, "id": "t1"}]))
    state[PROBE_PREMATURE_FINAL_COUNT] = 2

    assert middleware.after_model(state, runtime=None) == {PROBE_PREMATURE_FINAL_COUNT: 0}


def test_authorized_probe_still_rejects_final_before_close(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state[PROBE_STATUS] = PROBE_COMPLETION_AUTHORIZED

    update = middleware.after_model(state, runtime=None)

    assert update["jump_to"] == "model"
    assert "application_close_required" in update["messages"][-1].content


def test_third_premature_final_interrupts_with_liveness_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state[PROBE_PREMATURE_FINAL_COUNT] = 2
    captured_payload = {}

    def fake_interrupt(payload):
        captured_payload.update(payload)
        raise GraphInterrupt()

    monkeypatch.setattr("middleware.probe_lifecycle.interrupt", fake_interrupt)
    with pytest.raises(GraphInterrupt):
        middleware.after_model(state, runtime=None)

    assert captured_payload["kind"] == "liveness"
    assert captured_payload["attempts"] == 3
    assert captured_payload["page_visit_id"] == "visit-1"


def test_completion_authorization_reports_missing_leaf_and_then_succeeds(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    runtime = SimpleNamespace(state=state, tool_call_id="authorize-1")

    rejected = middleware.authorize_probe_completion(runtime)
    assert _payload(rejected)["authorized"] is False
    assert "unvisited_leaf:meituan/外卖/美食" in _payload(rejected)["missing"]

    state[PROBE_VISITED_LEAF_PATHS] = ("meituan/外卖/美食",)
    authorized = middleware.authorize_probe_completion(runtime)
    assert _payload(authorized) == {"authorized": True, "missing": [], "ok": True}
    assert authorized.update[PROBE_STATUS] == PROBE_COMPLETION_AUTHORIZED


def test_merchant_back_and_unauthorized_close_are_rejected(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    merchant_state = _state(path="meituan/外卖/美食/商家")
    merchant_state[MERCHANT_COLLECTION_DONE] = False

    back = middleware.wrap_tool_call(
        _request("navigate_back", merchant_state, next_path="meituan/外卖/美食"),
        lambda request: _tool_message("navigate_back"),
    )
    close = middleware.wrap_tool_call(
        _request("close_package", merchant_state, package_name="com.sankuai.meituan"),
        lambda request: _tool_message("close_package"),
    )

    assert _payload(back)["error_type"] == "merchant_collection_incomplete"
    assert _payload(close)["error_type"] == "probe_completion_not_authorized"


def test_authorized_close_transitions_to_completed(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state[PROBE_STATUS] = PROBE_COMPLETION_AUTHORIZED

    inner_result = Command(
        update={
            "messages": [_tool_message("close_package")],
            PAGE_CONTEXT_PATH: "",
        }
    )
    result = middleware.wrap_tool_call(
        _request("close_package", state, package_name="com.sankuai.meituan"),
        lambda request: inner_result,
    )

    assert isinstance(result, Command)
    assert result.update[PROBE_STATUS] == PROBE_COMPLETED


def test_successful_navigation_marks_configured_leaf_visited(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state(path="meituan")
    inner_result = Command(
        update={
            "messages": [_tool_message("navigate_to")],
            PAGE_CONTEXT_PATH: "meituan/外卖/美食",
        }
    )

    result = middleware.wrap_tool_call(
        _request("navigate_to", state, next_path="meituan/外卖/美食"),
        lambda request: inner_result,
    )

    assert result.update[PROBE_VISITED_LEAF_PATHS] == ("meituan/外卖/美食",)


def test_create_agent_loop_interrupts_after_three_toolless_probe_responses() -> None:
    class ToolBindingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, *_args, **_kwargs):
            return self

    from src.agent import build_agent
    runtime = build_agent(
        model=ToolBindingFakeModel(
            responses=[
                AIMessage(content="任务仍需继续"),
                AIMessage(content="系统仍有新增商品"),
                AIMessage(content="尚不允许结束"),
            ]
        ),
        tools=[],
    )

    result = runtime.run_turn(
        [{"role": "user", "content": "执行应用探测任务"}],
        session_id="liveness-integration",
        max_iterations=30,
    )

    assert result.stopped_by == "liveness_interrupt"
    assert result.interrupted is True
    assert runtime.pending_interrupt_kind("liveness-integration") == "liveness"
    assert "回复“继续”" in result.output
