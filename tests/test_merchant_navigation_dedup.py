"""Completed merchant identities survive list refreshes, not new probe runs."""
from page_strategy_helpers import page_controller


import asyncio
import json
from types import SimpleNamespace

import pytest

from src.conductor.state import initial_probe_state, reset_collection_state, merge_fields, initial_fields
from tests.test_collection_persistence_middleware import (
    _apply_state, _mark_trained, _middleware, _payload, _request as collection_request, _tool_message,
)
from page_strategy_helpers import mechanism as _mechanism, message as _message, request as _request, state as _state
from src.strategy.general.visits import PageVisitController


def test_only_successful_completion_registers_verified_merchant(tmp_path):
    collection, registry, _ = _middleware(tmp_path)
    request = collection_request("swipe_up")
    state = request.state
    runtime = SimpleNamespace(state=state, tool_call_id="done")
    assert 'probe_completed_merchants' not in collection.is_done(runtime).update
    _mark_trained(registry, mechanism=collection.service.mechanism)
    for step in range(4):
        result = collection.wrap_tool_call(request, lambda _: _tool_message("swipe_up", "cup"))
        _apply_state(state, result)
        assert 'probe_completed_merchants' not in result.update
        done = collection.is_done(runtime)
        assert _payload(done)["done"] == (step == 3)
        if step < 3:
            assert 'probe_completed_merchants' not in done.update
        _apply_state(state, done)
    path = state['general']['visit']['path']
    assert state['probe']['completed_merchants'] == {path: ("商家A",)}
    # This is idempotent and uses the collector-confirmed profile, not a card index.
    assert collection.is_done(runtime).update['probe']['completed_merchants'] == {path: ("商家A",)}
    state.update(merge_fields(state, reset_collection_state()))
    state["general"] = _state(path.rsplit("/", 1)[0])["general"]
    calls = []
    result = page_controller(mechanism=collection.service.mechanism).execute_operation(
        _request("navigate_to", state, x=999, y=888, next_path=path, merchant_name="商家A"),
        lambda _: calls.append("tap") or _message("navigate_to"),
    )
    assert not calls
    assert json.loads(result.content)["error_type"] == "invalid_navigation_target"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_duplicate_rejected_before_action_after_reorder(tmp_path, asynchronous):
    path = "meituan/外卖/美食/商家"
    state = _state("meituan/外卖/美食", **{'probe_completed_merchants': {path: ("Shop (East)",)}})
    middleware = page_controller(mechanism=_mechanism(tmp_path))
    calls = []
    request = _request("navigate_to", state, x=456, y=789, next_path=path, merchant_name=" Shop  (East) \n")
    if asynchronous:
        async def handler(_):
            calls.append("tap")
            return _message("navigate_to")
        result = asyncio.run(middleware.aexecute_operation(request, handler))
    else:
        result = middleware.execute_operation(request, lambda _: calls.append("tap") or _message("navigate_to"))
    assert calls == []
    assert result.status == "error"
    assert state['general']['visit']['visit_id'] == "visit-current"


def test_other_branch_unfinished_merchant_and_other_template_remain_eligible(tmp_path):
    path = "meituan/外卖/美食/商家"
    middleware = page_controller(mechanism=_mechanism(tmp_path))
    for completed, name in (({path: ("Shop (East)",)}, "Shop (West)"),
                            ({}, "Shop (East)"),
                            ({"meituan/美食/商家": ("Shop (East)",)}, "Shop (East)")):
        state = _state("meituan/外卖/美食", **{'probe_completed_merchants': completed})
        assert middleware._validate(_request(
            "navigate_to", state, x=10, y=20, next_path=path, merchant_name=name)) is None


def test_merchant_requires_identity_but_ordinary_navigation_does_not(tmp_path):
    middleware = page_controller(mechanism=_mechanism(tmp_path))
    for name in ("", " \n "):
        result = middleware._validate(_request(
            "navigate_to", _state("meituan/外卖/美食"),
            x=10, y=20, next_path="meituan/外卖/美食/商家", merchant_name=name))
        assert json.loads(result.content)["error_type"] == "invalid_navigation_target"
    assert middleware._validate(_request(
        "navigate_to", _state("meituan"), x=10, y=20, next_path="meituan/外卖")) is None


def test_new_probe_clears_completion_but_page_change_does_not():
    state = merge_fields(initial_fields(), initial_probe_state(("meituan/美食/商家",)))
    state.setdefault('probe', {})['completed_merchants'] = {"meituan/美食/商家": ("Shop",)}
    state.update(merge_fields(state, reset_collection_state()))
    assert state['probe']['completed_merchants']
    state.update(initial_probe_state(("meituan/美食/商家",)))
    assert state['probe']['completed_merchants'] == {}
