from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from middleware.page_context import (
    PAGE_CONTEXT_ACTIVE,
    PAGE_CONTEXT_APP_ROOT,
    PAGE_CONTEXT_OVERLAY,
    PAGE_CONTEXT_PATH,
    PAGE_CONTEXT_PENDING_PATH,
    PAGE_COLLECTION_STARTED,
    PAGE_PRODUCT_ENTRY_TAPS,
    PAGE_VISIT_ID,
    PageContextMiddleware,
    PageContextState,
    initial_page_context_state,
)
from middleware.probe_state import reset_collection_state
from page_mechanism import PageMechanism


POPUP_XML = '<h><n k="1" bounds="[200,500][880,1400]"/><n k="1" bounds="[800,520][850,570]"/></h>'
FULL_XML = '<h><n k="1" bounds="[0,0][1080,2400]"/><n k="1" bounds="[0,200][1080,2200]"/></h>'


@dataclass
class FakeModelRequest:
    state: dict
    tools: list
    system_message: SystemMessage | None = None
    model_settings: dict | None = None

    def override(self, **kwargs):
        return replace(self, **kwargs)


def _mechanism(tmp_path: Path) -> PageMechanism:
    root = tmp_path / "pages"
    for relative, page, task in (
        ("meituan", "home page", "home task"),
        ("meituan/外卖", "takeout page", "takeout task"),
        ("meituan/外卖/美食", "food page", "food task"),
        ("meituan/外卖/美食/商家", "merchant page", "merchant task"),
    ):
        directory = root / relative
        directory.mkdir(parents=True)
        (directory / "PAGE.md").write_text(page, encoding="utf-8")
        (directory / "task.md").write_text(task, encoding="utf-8")
    return PageMechanism(root)


def _state(path: str = "meituan/外卖", **overrides) -> dict:
    state = {
        PAGE_CONTEXT_ACTIVE: True,
        PAGE_CONTEXT_APP_ROOT: "meituan",
        PAGE_CONTEXT_PATH: path,
        PAGE_CONTEXT_PENDING_PATH: "",
        PAGE_CONTEXT_OVERLAY: False,
        PAGE_VISIT_ID: "visit-current",
        PAGE_PRODUCT_ENTRY_TAPS: 0,
        PAGE_COLLECTION_STARTED: False,
    }
    state.update(overrides)
    return state


def _request(tool_name: str, state: dict, **args):
    return SimpleNamespace(
        tool_call={"name": tool_name, "id": f"{tool_name}-1", "args": args},
        tool=None,
        state=state,
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "thread-1"}}),
    )


def _message(name: str, content: str = FULL_XML) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=f"{name}-1", name=name)


def test_initial_state_activates_context_without_manual_phase() -> None:
    assert initial_page_context_state() == {
        PAGE_CONTEXT_ACTIVE: True,
        PAGE_CONTEXT_APP_ROOT: "",
        PAGE_CONTEXT_PATH: "",
        PAGE_CONTEXT_PENDING_PATH: "",
        PAGE_CONTEXT_OVERLAY: False,
        PAGE_VISIT_ID: "",
        PAGE_PRODUCT_ENTRY_TAPS: 0,
        PAGE_COLLECTION_STARTED: False,
        **reset_collection_state(),
    }


def test_model_request_keeps_base_system_prompt_stable(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    request = FakeModelRequest(
        state=_state(),
        tools=[SimpleNamespace(name="tap_in_page"), SimpleNamespace(name="navigate_to")],
        system_message=SystemMessage(content="base"),
        model_settings={"temperature": 0},
    )

    prepared = middleware._prepare_model_request(request)
    assert prepared.system_message is request.system_message
    assert prepared.system_message.content == "base"
    assert prepared.model_settings["parallel_tool_calls"] is False


def test_prelaunch_model_request_keeps_complete_tool_contract(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    tools = [
        SimpleNamespace(name=name)
        for name in ("run_package", "uiautomate", "navigate_to", "prepare_collector")
    ]
    request = FakeModelRequest(
        state=_state(path="", **{PAGE_CONTEXT_APP_ROOT: "", PAGE_VISIT_ID: ""}),
        tools=tools,
        system_message=SystemMessage(content="base"),
    )

    prepared = middleware._prepare_model_request(request)

    assert prepared.tools is tools
    assert [tool.name for tool in prepared.tools] == [
        "run_package",
        "uiautomate",
        "navigate_to",
        "prepare_collector",
    ]
    assert prepared.model_settings["parallel_tool_calls"] is False


def test_run_package_sets_validated_home_path(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    result = middleware.wrap_tool_call(
        _request("run_package", _state(path="", **{PAGE_CONTEXT_APP_ROOT: ""}), package_name="com.sankuai.meituan"),
        lambda _: _message("run_package", "started"),
    )

    assert isinstance(result, Command)
    assert result.update[PAGE_CONTEXT_PATH] == "meituan"
    assert result.update[PAGE_CONTEXT_APP_ROOT] == "meituan"
    assert result.update[PAGE_VISIT_ID]


def test_tap_in_page_keeps_current_path(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    result = middleware.wrap_tool_call(
        _request("tap_in_page", state, x=1, y=2),
        lambda _: _message("tap_in_page"),
    )

    assert isinstance(result, ToolMessage)
    assert state[PAGE_CONTEXT_PATH] == "meituan/外卖"


def test_navigation_validates_before_device_and_commits_target(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    calls: list[str] = []
    invalid = middleware.wrap_tool_call(
        _request("navigate_to", _state(), x=1, y=2, next_path="美食"),
        lambda _: calls.append("invalid") or _message("navigate_to"),
    )
    valid = middleware.wrap_tool_call(
        _request("navigate_to", _state(), x=1, y=2, next_path="meituan/外卖/美食"),
        lambda _: calls.append("valid") or _message("navigate_to"),
    )

    assert invalid.status == "error"
    assert json.loads(invalid.content)["error_type"] == "invalid_navigation_target"
    assert calls == ["valid"]
    assert valid.update[PAGE_CONTEXT_PATH] == "meituan/外卖/美食"
    assert valid.update[PAGE_VISIT_ID] != _state()[PAGE_VISIT_ID]


def test_navigation_popup_defers_target_until_same_page_close(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    navigation = middleware.wrap_tool_call(
        _request("navigate_to", state, x=1, y=2, next_path="meituan/外卖/美食"),
        lambda _: _message("navigate_to", POPUP_XML),
    )
    overlay_state = {**state, **navigation.update}
    closed = middleware.wrap_tool_call(
        _request("tap_in_page", overlay_state, x=800, y=520),
        lambda _: _message("tap_in_page", FULL_XML),
    )

    assert PAGE_CONTEXT_PATH not in navigation.update
    assert navigation.update[PAGE_CONTEXT_PENDING_PATH] == "meituan/外卖/美食"
    assert navigation.update[PAGE_CONTEXT_OVERLAY] is True
    assert closed.update[PAGE_CONTEXT_PATH] == "meituan/外卖/美食"
    assert closed.update[PAGE_CONTEXT_PENDING_PATH] == ""
    assert closed.update[PAGE_CONTEXT_OVERLAY] is False
    assert closed.update[PAGE_VISIT_ID] != state[PAGE_VISIT_ID]


def test_navigate_back_commits_declared_absolute_path(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    result = middleware.wrap_tool_call(
        _request(
            "navigate_back",
            _state(path="meituan/外卖/美食"),
            next_path="meituan/外卖",
        ),
        lambda _: _message("navigate_back"),
    )

    assert result.update[PAGE_CONTEXT_PATH] == "meituan/外卖"
    assert result.update[PAGE_CONTEXT_PENDING_PATH] == ""
    assert result.update[PAGE_VISIT_ID] != "visit-current"


def test_merchant_allows_one_entry_tap_before_scrolling_then_blocks_taps(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    merchant_state = _state(path="meituan/外卖/美食/商家")

    first_tap = middleware.wrap_tool_call(
        _request("tap_in_page", merchant_state, x=1, y=2),
        lambda _: _message("tap_in_page"),
    )
    after_tap = {**merchant_state, **first_tap.update}
    second_tap = middleware.wrap_tool_call(
        _request("tap_in_page", after_tap, x=1, y=2),
        lambda _: _message("tap_in_page"),
    )

    assert first_tap.update[PAGE_PRODUCT_ENTRY_TAPS] == 1
    assert second_tap.status == "error"
    assert json.loads(second_tap.content)["error_type"] == "merchant_entry_tap_limit"

    scrolling = middleware.wrap_tool_call(
        _request("swipe_up", merchant_state, x=500, y=1200),
        lambda _: _message("swipe_up"),
    )
    after_scroll = {**merchant_state, **scrolling.update}
    blocked = middleware.wrap_tool_call(
        _request("tap_in_page", after_scroll, x=1, y=2),
        lambda _: _message("tap_in_page"),
    )
    assert scrolling.update[PAGE_COLLECTION_STARTED] is True
    assert json.loads(blocked.content)["error_type"] == "merchant_collection_already_started"


def test_overlay_close_does_not_consume_merchant_entry_tap(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    state = _state(
        path="meituan/外卖/美食/商家",
        **{PAGE_CONTEXT_OVERLAY: True},
    )
    result = middleware.wrap_tool_call(
        _request("tap_in_page", state, x=1, y=2),
        lambda _: _message("tap_in_page", FULL_XML),
    )

    assert result.update[PAGE_CONTEXT_OVERLAY] is False
    assert PAGE_PRODUCT_ENTRY_TAPS not in result.update


def test_overlay_keeps_model_tool_contract_stable(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    tools = [SimpleNamespace(name=name) for name in ("tap_in_page", "navigate_to", "uiautomate", "notify_user")]
    request = FakeModelRequest(
        state=_state(**{PAGE_CONTEXT_OVERLAY: True}),
        tools=tools,
        system_message=SystemMessage(content="base"),
    )

    prepared = middleware._prepare_model_request(request)

    assert prepared.tools is tools
    assert prepared.system_message is request.system_message


def test_only_first_device_action_from_one_ai_message_executes(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state["messages"] = [AIMessage(content="", id="ai-step", tool_calls=[])]
    calls: list[str] = []

    first = middleware.wrap_tool_call(
        _request("uiautomate", state),
        lambda _: calls.append("first") or _message("uiautomate"),
    )
    second = middleware.wrap_tool_call(
        _request("swipe_up", state, x=1, y=1000),
        lambda _: calls.append("second") or _message("swipe_up"),
    )

    assert isinstance(first, ToolMessage)
    assert second.status == "error"
    assert json.loads(second.content)["error_type"] == "multiple_device_actions"
    assert calls == ["first"]


def test_device_action_limit_does_not_require_ai_message_id(tmp_path: Path) -> None:
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    state = _state()
    state["messages"] = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "uiautomate", "args": {}, "id": "call-1", "type": "tool_call"},
                {"name": "swipe_up", "args": {"x": 1, "y": 1000}, "id": "call-2", "type": "tool_call"},
            ],
        )
    ]

    first = middleware.wrap_tool_call(_request("uiautomate", state), lambda _: _message("uiautomate"))
    second = middleware.wrap_tool_call(_request("swipe_up", state, x=1, y=1000), lambda _: _message("swipe_up"))

    assert isinstance(first, ToolMessage)
    assert second.status == "error"


def test_page_context_reducers_accept_parallel_graph_updates() -> None:
    builder = StateGraph(PageContextState)
    builder.add_node("first", lambda _state: {PAGE_CONTEXT_PENDING_PATH: "meituan/外卖"})
    builder.add_node("second", lambda _state: {PAGE_CONTEXT_PENDING_PATH: "meituan/外卖"})
    builder.add_edge(START, "first")
    builder.add_edge(START, "second")
    builder.add_edge("first", END)
    builder.add_edge("second", END)

    result = builder.compile().invoke(initial_page_context_state())

    assert result[PAGE_CONTEXT_PENDING_PATH] == "meituan/外卖"
