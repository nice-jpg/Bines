from __future__ import annotations

from page_strategy_helpers import new_visit

from src.conductor.state import read_conductor_state, merge_fields

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from gateway_helpers import GatewayHandle, RecordingChannel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, RemoveMessage, ToolMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from page_helpers import page_mechanism

from src.agent_runtime.types.execution import EngineStatus, InterruptKind
from src.conductor.engine import ConductorEngine
from src.conductor.state import PROBE_COMPLETED, PROBE_COMPLETION_AUTHORIZED, PROBE_RUNNING
from src.middleware.probe.lifecycle import ProbeLifecycleMiddleware
from src.page.mechanism import PageMechanism



def _mechanism(tmp_path: Path) -> PageMechanism:
    for relative in ("meituan/外卖/美食", "meituan/外卖/美食/商家"):
        page = tmp_path / relative
        page.mkdir(parents=True)
        (page / "PAGE.md").write_text("# Page\n", encoding="utf-8")
        (page / "task.md").write_text("# Task\n", encoding="utf-8")
    return page_mechanism(tmp_path)


def _state(*, message: AIMessage | None = None, path: str = "meituan/外卖/美食") -> dict:
    return read_conductor_state({
        'probe_status': PROBE_RUNNING,
        'probe_premature_final_count': 0,
        'probe_target_leaf_paths': ("meituan/外卖/美食",),
        'probe_visited_leaf_paths': (),
        'page_context_path': path,
        'page_context_pending_path': "",
        'page_visit_id': "visit-1",
        "messages": [message or AIMessage(content="任务还需继续", id="ai-1")],
     "page_visit": new_visit("merchant" if path.endswith("/商家") else "general")})


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


def test_recorded_recovery_obligation_blocks_completion_until_resolved(tmp_path):
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state.setdefault('probe', {})['unfinished_merchants'] = {"visit-1": {"path": "meituan/外卖/美食/商家", "merchant": "Shop"}}
    assert "unfinished_merchants_require_recovery" in middleware._completion_evaluation(state)[0]["missing"]
    state.setdefault('general', {}).setdefault('visit', {})['path'] = "meituan/外卖/美食/商家"
    state.setdefault('probe', {})['current_merchant'] = "Shop"
    state["general"]["visit"]["strategy"] = "merchant"
    completed = middleware.wrap_tool_call(_request("is_done", state), lambda _: Command(update={
        "messages": [_tool_message("is_done")], "merchant": {"visit_id": "visit-1", "phase": "completed", "entry_taps": 0, "training_version": "", "evidence": {}}}))
    assert completed.update['probe']['unfinished_merchants'] == {}


def test_authorization_is_revoked_after_navigation(tmp_path):
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state.setdefault('probe', {})['status'] = PROBE_COMPLETION_AUTHORIZED
    result = middleware.wrap_tool_call(_request("navigate_to", state), lambda _: Command(update={
        "messages": [_tool_message("navigate_to")], "general": {**state["general"], "visit": {"path": "meituan/外卖/美食/商家", "visit_id": "next", "strategy": "merchant"}}}))
    assert result.update["probe"]["status"] == PROBE_RUNNING


def test_repeated_failed_calls_trigger_liveness_even_with_tool_calls(tmp_path, monkeypatch):
    def stop(payload):
        assert payload["reason"] == "repeated_tool_failures"
        raise GraphInterrupt()
    monkeypatch.setattr("src.middleware.probe.lifecycle.interrupt", stop)
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state(message=AIMessage(content="", tool_calls=[{"name": "close_package", "args": {}, "id": "call"}]))
    for _ in range(3):
        result = middleware.wrap_tool_call(_request("close_package", state), lambda _: pytest.fail("must not execute"))
        state.update({key: value for key, value in result.update.items() if key != "messages"})
    with pytest.raises(GraphInterrupt):
        middleware.after_model(state, runtime=None)


def test_premature_final_is_removed_and_jumps_back_to_model(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))

    update = middleware.after_model(_state(), runtime=None)

    assert update["jump_to"] == "model"
    assert update['probe']['premature_final_count'] == 1
    assert isinstance(update["messages"][0], RemoveMessage)
    assert "configured_leaf_pages_unvisited" in update["messages"][1].content


def test_tool_call_resets_consecutive_premature_final_count(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state(message=AIMessage(content="", tool_calls=[{"name": "uiautomate", "args": {}, "id": "t1"}]))
    state.setdefault('probe', {})['premature_final_count'] = 2

    assert middleware.after_model(state, runtime=None)['probe']['premature_final_count'] == 0


def test_authorized_probe_still_rejects_final_before_close(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state.setdefault('probe', {})['status'] = PROBE_COMPLETION_AUTHORIZED

    update = middleware.after_model(state, runtime=None)

    assert update["jump_to"] == "model"
    assert "application_close_required" in update["messages"][-1].content


def test_third_premature_final_interrupts_with_liveness_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state.setdefault('probe', {})['premature_final_count'] = 2
    captured_payload = {}

    def fake_interrupt(payload):
        captured_payload.update(payload)
        raise GraphInterrupt()

    monkeypatch.setattr("src.middleware.probe.lifecycle.interrupt", fake_interrupt)
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

    state.setdefault('probe', {})['visited_leaf_paths'] = ("meituan/外卖/美食",)
    authorized = middleware.authorize_probe_completion(runtime)
    assert _payload(authorized) == {"authorized": True, "missing": [], "ok": True}
    assert authorized.update['probe']['status'] == PROBE_COMPLETION_AUTHORIZED


def test_lifecycle_delegates_page_departure_but_rejects_unauthorized_close(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    merchant_state = _state(path="meituan/外卖/美食/商家")

    back = middleware.wrap_tool_call(
        _request("navigate_back", merchant_state, next_path="meituan/外卖/美食"),
        lambda request: _tool_message("navigate_back"),
    )
    close = middleware.wrap_tool_call(
        _request("close_package", merchant_state, package_name="com.sankuai.meituan"),
        lambda request: _tool_message("close_package"),
    )

    assert back.update["messages"][0].content == "ok"
    assert _payload(close)["error_type"] == "probe_completion_not_authorized"


def test_authorized_close_transitions_to_completed(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state.setdefault('probe', {})['status'] = PROBE_COMPLETION_AUTHORIZED

    inner_result = Command(
        update={"messages": [_tool_message("close_package")], "general": {"active": False, "app_root": "", "visit": None, "pending": None}}
    )
    result = middleware.wrap_tool_call(
        _request("close_package", state, package_name="com.sankuai.meituan"),
        lambda request: inner_result,
    )

    assert isinstance(result, Command)
    assert result.update['probe']['status'] == PROBE_COMPLETED


def test_successful_navigation_marks_configured_leaf_visited(tmp_path: Path) -> None:
    middleware = ProbeLifecycleMiddleware(mechanism=_mechanism(tmp_path))
    state = _state(path="meituan")
    inner_result = Command(
        update=read_conductor_state({
            "messages": [_tool_message("navigate_to")],
            'page_context_path': "meituan/外卖/美食",
         "page_visit": new_visit("merchant" if "meituan/外卖/美食".endswith("/商家") else "general"), "page_visit_id": "test-visit"})
    )

    result = middleware.wrap_tool_call(
        _request("navigate_to", state, next_path="meituan/外卖/美食"),
        lambda request: inner_result,
    )

    assert result.update['probe']['visited_leaf_paths'] == ("meituan/外卖/美食",)


def test_create_agent_loop_interrupts_after_three_toolless_probe_responses() -> None:
    class ToolBindingFakeModel(FakeMessagesListChatModel):
        def bind_tools(self, *_args, **_kwargs):
            return self

    class ProbeEngine(ConductorEngine):
        def _create_initial_messages(self):
            return [{"role": "user", "content": "执行应用探测任务"}]

    runtime = ProbeEngine(
        model=ToolBindingFakeModel(
            responses=[
                AIMessage(content="任务仍需继续"),
                AIMessage(content="系统仍有新增商品"),
                AIMessage(content="尚不允许结束"),
            ]
        ),
        gateway_id=GatewayHandle(RecordingChannel()).gateway_id, max_iterations=30,
        session_id="liveness-integration",
    )

    result = runtime.run_turn()
    assert result.status == EngineStatus.INTERRUPTED
    assert result.interrupt_kind == InterruptKind.LIVENESS
    assert "回复“继续”" in result.output
