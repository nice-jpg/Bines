"""Completed merchant identities survive list refreshes, not new probe runs."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.middleware.probe_state import PROBE_COMPLETED_MERCHANTS, initial_probe_state, reset_collection_state
from tests.test_collection_persistence_middleware import (
    _apply_state, _mark_trained, _middleware, _payload, _request as collection_request, _tool_message,
)
from tests.test_page_context_middleware import (
    PageContextMiddleware, _mechanism, _message, _request, _state,
)


def test_only_successful_completion_registers_verified_merchant(tmp_path):
    collection, registry, _ = _middleware(tmp_path)
    request = collection_request("swipe_up")
    state = request.state
    runtime = SimpleNamespace(state=state, tool_call_id="done")
    assert PROBE_COMPLETED_MERCHANTS not in collection.is_done(runtime).update
    _mark_trained(registry)
    for step in range(4):
        result = collection.wrap_tool_call(request, lambda _: _tool_message("swipe_up", "cup"))
        _apply_state(state, result)
        assert PROBE_COMPLETED_MERCHANTS not in result.update
        done = collection.is_done(runtime)
        assert _payload(done)["done"] == (step == 3)
        if step < 3:
            assert PROBE_COMPLETED_MERCHANTS not in done.update
        _apply_state(state, done)
    path = state["page_context_path"]
    assert state[PROBE_COMPLETED_MERCHANTS] == {path: ("商家A",)}
    # This is idempotent and uses the collector-confirmed profile, not a card index.
    assert collection.is_done(runtime).update[PROBE_COMPLETED_MERCHANTS] == {path: ("商家A",)}
    state.update(reset_collection_state())
    state.update(_state(path.rsplit("/", 1)[0]))
    calls = []
    result = PageContextMiddleware(mechanism=collection.service.mechanism).wrap_tool_call(
        _request("navigate_to", state, x=999, y=888, next_path=path, merchant_name="商家A"),
        lambda _: calls.append("tap") or _message("navigate_to"),
    )
    assert not calls
    assert json.loads(result.content)["error_type"] == "merchant_already_collected"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_duplicate_rejected_before_action_after_reorder(tmp_path, asynchronous):
    path = "meituan/外卖/美食/商家"
    state = _state("meituan/外卖/美食", **{PROBE_COMPLETED_MERCHANTS: {path: ("Shop (East)",)}})
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    calls = []
    request = _request("navigate_to", state, x=456, y=789, next_path=path, merchant_name=" Shop  (East) \n")
    if asynchronous:
        async def handler(_):
            calls.append("tap")
            return _message("navigate_to")
        result = asyncio.run(middleware.awrap_tool_call(request, handler))
    else:
        result = middleware.wrap_tool_call(request, lambda _: calls.append("tap") or _message("navigate_to"))
    assert calls == []
    assert result.status == "error"
    assert state["page_visit_id"] == "visit-current"


def test_other_branch_unfinished_merchant_and_other_template_remain_eligible(tmp_path):
    path = "meituan/外卖/美食/商家"
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    for completed, name in (({path: ("Shop (East)",)}, "Shop (West)"),
                            ({}, "Shop (East)"),
                            ({"meituan/美食/商家": ("Shop (East)",)}, "Shop (East)")):
        state = _state("meituan/外卖/美食", **{PROBE_COMPLETED_MERCHANTS: completed})
        assert middleware._validate_tool_call(_request(
            "navigate_to", state, x=10, y=20, next_path=path, merchant_name=name)) is None


def test_merchant_requires_identity_but_ordinary_navigation_does_not(tmp_path):
    middleware = PageContextMiddleware(mechanism=_mechanism(tmp_path))
    for name in ("", " \n "):
        result = middleware._validate_tool_call(_request(
            "navigate_to", _state("meituan/外卖/美食"),
            x=10, y=20, next_path="meituan/外卖/美食/商家", merchant_name=name))
        assert json.loads(result.content)["error_type"] == "merchant_identity_required"
    assert middleware._validate_tool_call(_request(
        "navigate_to", _state("meituan"), x=10, y=20, next_path="meituan/外卖")) is None


def test_new_probe_clears_completion_but_page_change_does_not():
    state = initial_probe_state(("meituan/美食/商家",))
    state[PROBE_COMPLETED_MERCHANTS] = {"meituan/美食/商家": ("Shop",)}
    state.update(reset_collection_state())
    assert state[PROBE_COMPLETED_MERCHANTS]
    state.update(initial_probe_state(("meituan/美食/商家",)))
    assert state[PROBE_COMPLETED_MERCHANTS] == {}
