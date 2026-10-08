from __future__ import annotations

import asyncio
import hashlib
import json
from threading import RLock

import pytest
from langchain.tools import ToolRuntime

from src.agent_runtime.communication import RuntimeCommunication
from src.collection.continuous import ContinuousCollection
from src.tools.event_hub import CollectionSwipe, SwipeObservation
from src.probe_state import (
    COLLECTION_CONSECUTIVE_EMPTY_SWIPES, COLLECTION_LATEST_NEW_COUNT,
    COLLECTION_VISIT_SUMMARIES, MERCHANT_COLLECTION_DONE, PROBE_COMPLETED_MERCHANTS,
)
from test_collection_persistence_middleware import _middleware, _tool_message


class ScriptedHub:
    def __init__(self, observations):
        self.operation_lock = RLock()
        self.observations = iter(observations)
        self.calls = []
        self.on_swipe = lambda: None

    def collection_swipe(self, action, x, y):
        self.calls.append((action, x, y))
        self.on_swipe()
        return next(self.observations)


def observation(name="cup", ratio=0.2):
    return SwipeObservation({"kind": "page_change_hint", "update_ratio": ratio, "regions": []},
                            _tool_message("swipe_up", name).artifact["page_snapshot"], ratio)


def setup_loop(tmp_path, observations):
    middleware, registry, workbook = _middleware(tmp_path)
    service = middleware.service
    path, visit = "meituan/美食/商家", "visit-a"
    registry.mark_trained(visit, path, merchant_profile={"name": "Merchant"},
                         script_fingerprint=hashlib.sha256(service.mechanism.read_run_agent_py(path).encode()).hexdigest(),
                         contract_fingerprint=service.mechanism.collection_contract(path).fingerprint)
    registry.set_swipe_threshold(visit, 0.55, 2)
    hub = ScriptedHub(observations)
    loop = ContinuousCollection(service, hub)
    state = {"page_context_path": path, "page_visit_id": visit, "messages": []}
    runtime = ToolRuntime(state=state, context=None, config={}, stream_writer=lambda _: None,
                          tool_call_id="loop", store=None)
    return loop, runtime, hub, workbook


def payload(result):
    return json.loads(result.update["messages"][0].content)


def test_long_swipes_complete_with_one_message_and_shared_state(tmp_path):
    loop, runtime, hub, workbook = setup_loop(tmp_path, [observation()] * 4)
    result = loop.collect_continuously(500, 1200, .55, runtime)
    assert payload(result)["status"] == "completed"
    assert payload(result)["appended_items"] == 1
    assert len(result.update["messages"]) == 1
    assert result.update[COLLECTION_CONSECUTIVE_EMPTY_SWIPES] == 3
    assert result.update[MERCHANT_COLLECTION_DONE]
    assert result.update["page_collection_started"]
    assert result.update[COLLECTION_VISIT_SUMMARIES]["visit-a"]["collected_items"] == 1
    assert result.update[PROBE_COMPLETED_MERCHANTS][runtime.state["page_context_path"]] == ("Merchant",)
    assert all(action == CollectionSwipe.LONG for action, _, _ in hub.calls)
    assert workbook.exists()
    runtime.state.update(result.update)
    assert loop.service.completion(runtime.state).done
    again = loop.collect_continuously(500, 1200, .55, runtime)
    assert payload(again)["swipes"] == 0


def test_partial_rows_short_swipe_until_complete_then_return_to_long(tmp_path, monkeypatch):
    loop, runtime, hub, _ = setup_loop(tmp_path, [observation()] * 7)
    outputs = iter([
        {"name": "cup", "price": "10"},
        {"name": "bowl", "price": ""},
        {"name": "bowl", "price": None},
        {"name": "bowl", "price": "20"},
        {"name": "bowl", "price": "20"},
        {"name": "bowl", "price": "20"},
        {"name": "bowl", "price": "20"},
    ])
    monkeypatch.setattr(loop.service.mechanism, "run_page_script",
                        lambda *args: {"ok": True, "result": {"items": [next(outputs)]}})
    result = loop.collect_continuously(500, 1200, .55, runtime)
    assert payload(result)["status"] == "completed"
    assert payload(result)["appended_items"] == 2
    assert [action for action, _, _ in hub.calls] == [
        CollectionSwipe.LONG, CollectionSwipe.LONG, CollectionSwipe.SHORT,
        CollectionSwipe.SHORT, CollectionSwipe.LONG, CollectionSwipe.LONG, CollectionSwipe.LONG]
    assert len(result.update["messages"]) == 1


@pytest.mark.parametrize("ratio", [.56, None, float("nan")])
def test_abnormal_change_never_runs_script(tmp_path, monkeypatch, ratio):
    loop, runtime, hub, _ = setup_loop(tmp_path, [observation(ratio=ratio)])
    monkeypatch.setattr(loop.service.mechanism, "run_page_script", lambda *a: pytest.fail("abnormal page must not collect"))
    result = loop.collect_continuously(500, 1200, .55, runtime)
    assert payload(result)["status"] == "attention_required"
    assert len(hub.calls) == 1
    assert result.update[COLLECTION_CONSECUTIVE_EMPTY_SWIPES] == 0
    if ratio == .56:
        assert "page_hint" not in payload(result)


def test_equal_threshold_is_valid(tmp_path):
    loop, runtime, _, _ = setup_loop(tmp_path, [observation(ratio=.55)] * 4)
    assert payload(loop.collect_continuously(500, 1200, .55, runtime))["status"] == "completed"


@pytest.mark.parametrize("problem", ["threshold", "visit", "script", "contract", "missing_profile"])
def test_invalid_training_or_parameters_never_move(tmp_path, problem):
    loop, runtime, hub, _ = setup_loop(tmp_path, [])
    threshold = .55
    if problem == "threshold":
        threshold = .56
    elif problem == "visit":
        runtime.state["page_visit_id"] = "different-visit"
    elif problem == "script":
        loop.service.mechanism.write_run_agent_py(runtime.state["page_context_path"], "def run(view):\n return {}\n")
    elif problem == "missing_profile":
        record = loop.service.training_registry.record_for("visit-a")
        loop.service.training_registry.mark_trained("visit-a", record.page_path)
    else:
        from src.collection_contract import CollectionContract
        loop.service.mechanism.save_collection_contract(runtime.state["page_context_path"],
            CollectionContract.from_dict({"primary_key": ["name"], "required_fields": ["name"]}))
    result = loop.collect_continuously(500, 1200, threshold, runtime)
    assert payload(result)["status"] == "attention_required"
    assert not hub.calls


@pytest.mark.parametrize("close", [False, True])
def test_input_and_shutdown_yield_after_step_without_consuming_message(tmp_path, close):
    loop, runtime, hub, _ = setup_loop(tmp_path, [observation()])
    channel = RuntimeCommunication(lambda _: True)
    runtime.context = channel._root
    hub.on_swipe = channel.close if close else lambda: channel.receive("change the task")
    result = loop.collect_continuously(500, 1200, .55, runtime)
    assert payload(result)["status"] == "yielded"
    assert payload(result)["appended_items"] == 1
    assert len(hub.calls) == 1
    if not close:
        assert channel.pending
        assert len(channel._root.pending([])) == 1


def test_pending_input_before_first_swipe_never_moves(tmp_path):
    loop, runtime, hub, _ = setup_loop(tmp_path, [])
    channel = RuntimeCommunication(lambda _: True)
    runtime.context = channel._root
    channel.receive("stop collecting")
    result = loop.collect_continuously(500, 1200, .55, runtime)
    assert payload(result)["status"] == "yielded"
    assert not hub.calls


def test_persistence_failure_returns_prior_progress(tmp_path, monkeypatch):
    loop, runtime, hub, _ = setup_loop(tmp_path, [observation("cup"), observation("bowl")])
    persist = loop.service.workbook_sink.persist_run_result
    calls = 0
    def save(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return persist(**kwargs)
    monkeypatch.setattr(loop.service.workbook_sink, "persist_run_result", save)
    result = loop.collect_continuously(500, 1200, .55, runtime)
    assert payload(result)["status"] == "attention_required"
    assert payload(result)["appended_items"] == 1
    assert result.update[COLLECTION_LATEST_NEW_COUNT] == 1
    assert not result.update[MERCHANT_COLLECTION_DONE]
    assert len(hub.calls) == 2


@pytest.mark.parametrize("output", [{"ok": True, "result": {}}, {"ok": False, "error_type": "MissingPageAlias"}])
def test_invalid_script_output_does_not_become_empty_collection(tmp_path, monkeypatch, output):
    loop, runtime, hub, _ = setup_loop(tmp_path, [observation()])
    monkeypatch.setattr(loop.service.mechanism, "run_page_script", lambda *args: output)
    result = loop.collect_continuously(500, 1200, .55, runtime)
    assert payload(result)["status"] == "attention_required"
    assert payload(result)["appended_items"] == 0
    assert not result.update[MERCHANT_COLLECTION_DONE]
    assert len(hub.calls) == 1


def test_tool_schema_and_async_execution(tmp_path):
    loop, runtime, _, _ = setup_loop(tmp_path, [observation()] * 4)
    tool = loop.create_tool()
    assert set(tool.tool_call_schema.model_json_schema()["properties"]) == {"x", "y", "change_threshold"}
    result = asyncio.run(tool.ainvoke({"x": 500, "y": 1200, "change_threshold": .55, "runtime": runtime}))
    assert payload(result)["status"] == "completed"


def test_real_agent_tool_loop_commits_state_without_double_collection(tmp_path):
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.tools import StructuredTool
    from src.agent_runtime import create_runtime
    from src.middleware.probe_control import ProbeControlMiddleware
    from src.middleware.collection_persistence import CollectionPersistenceMiddleware
    from test_runtime_communication import Model
    loop, tool_runtime, hub, _ = setup_loop(tmp_path, [observation()] * 4)
    extra_calls = []
    def swipe_up() -> str:
        """A second device action must not run in this same model response."""
        extra_calls.append(True)
        return "unexpected"
    model = Model(replies=[AIMessage(content="", tool_calls=[
        {"name": "collect_continuously", "args": {"x": 500, "y": 1200, "change_threshold": .55}, "id": "collect"},
        {"name": "swipe_up", "args": {}, "id": "extra"},
    ]), AIMessage(content="done")])
    agent = create_runtime(name="test", model=model, tools=[loop.create_tool(), StructuredTool.from_function(swipe_up)],
        system_prompt="base", middleware=[
            ProbeControlMiddleware(mechanism=loop.service.mechanism, training_registry=loop.service.training_registry),
            CollectionPersistenceMiddleware(loop.service)])
    state = agent.invoke({**tool_runtime.state, "page_context_active": True, "page_context_app_root": "meituan",
                          "messages": [HumanMessage(content="collect")]})
    assert state[MERCHANT_COLLECTION_DONE], "\n".join(str(m.content) for m in state["messages"])
    assert state[COLLECTION_VISIT_SUMMARIES]["visit-a"]["collected_items"] == 1
    assert state["page_collection_started"]
    assert len(hub.calls) == 4 and not extra_calls
    assert len(model.seen) == 2


def test_loop_device_lock_blocks_concurrent_action(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    loop, runtime, hub, _ = setup_loop(tmp_path, [observation()] * 4)
    entered, release, competitor_waiting, competitor_done = (Event() for _ in range(4))
    def first_swipe():
        entered.set()
        assert release.wait(5)
    hub.on_swipe = first_swipe
    def competing_action():
        competitor_waiting.set()
        with hub.operation_lock:
            competitor_done.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        work = pool.submit(loop.collect_continuously, 500, 1200, .55, runtime)
        assert entered.wait(5)
        competing = pool.submit(competing_action)
        assert competitor_waiting.wait(5)
        assert not competitor_done.is_set()
        release.set()
        assert payload(work.result(timeout=5))["status"] == "completed"
        competing.result(timeout=5)
    assert competitor_done.is_set()
