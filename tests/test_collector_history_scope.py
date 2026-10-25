"""Evidence follows a checkpointed probe run, not a process, visit or thread alone."""

import json

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from page_helpers import collector_history
from src.collector.history import CollectorHistory
from src.conductor.state import ConductorState, initial_probe_state, reset_collection_state


def record(run_id, case_id="case"):
    return {"run_id": run_id, "case_id": case_id, "page_visit_id": "visit",
            "image": "data:image/png;base64,evidence", "baseline": {"items": []},
            "snapshot": {"xml": "<hierarchy />", "aliases": {}, "snapshot_id": case_id, "captured_at": "now"},
            "schema": {"items": []}, "revision": 0, "random_state": False, "guide_fingerprint": "guide"}


def test_history_isolates_runs_pages_and_legacy_records(tmp_path):
    history = collector_history(tmp_path)
    legacy = tmp_path / "app/page/old/case/record.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("obsolete and not even valid JSON")
    history.commit("app/page", "visit", [record("first")], [], run_id="first")
    history.commit("app/page/child", "visit", [record("first", "child")], [], run_id="first")
    history.commit("app/page", "visit", [record("second")], [], run_id="second")
    assert [c["case_id"] for c in history.load("app/page", run_id="first")] == ["case"]
    assert [c["run_id"] for c in history.load("app/page", run_id="second")] == ["second"]
    assert not history.load("app/page", run_id="third")
    assert legacy.read_text() == "obsolete and not even valid JSON"


def test_other_run_cannot_be_written_or_loaded_in_current_namespace(tmp_path):
    history = collector_history(tmp_path)
    with pytest.raises(ValueError, match="another probe run"):
        history.commit("app", "visit", [record("other")], [], run_id="current")
    history.commit("app", "visit", [record("current")], [], run_id="current")
    file = tmp_path / "runs/current/app/visit/case/record.json"
    data = json.loads(file.read_text())
    data["run_id"] = "other"
    file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Unsupported or incomplete"):
        history.load("app", run_id="current")


def test_guide_retirement_is_scoped_and_not_reversed_by_reusing_old_guide(tmp_path):
    history = collector_history(tmp_path)
    history.commit("app/page", "visit", [record("first")], [], run_id="first")
    history.commit("app/page/child", "visit", [record("first", "child")], [], run_id="first")
    history.commit("app/page", "visit", [record("second")], [], run_id="second")
    history.retire_other_guides("app/page", "new-guide", run_id="first")
    assert not history.load("app/page", run_id="first")
    assert len(history.load("app/page/child", run_id="first")) == 1
    assert len(history.load("app/page", run_id="second")) == 1
    history.retire_other_guides("app/page", "guide", run_id="first")
    assert not history.load("app/page", run_id="first")


@pytest.mark.parametrize("run_id", ["", "..", ".", "../other", "/tmp/other"])
def test_run_scope_cannot_escape_history_root(tmp_path, run_id):
    with pytest.raises(ValueError, match="run ID"):
        collector_history(tmp_path).load("app", run_id=run_id)


def test_checkpoint_keeps_run_across_invokes_and_failed_node_resume():
    observed = []

    def model(state):
        observed.append(state['probe']['run_id'])
        if len(observed) == 1:
            raise ConnectionError("disconnected")
        return {}

    graph = StateGraph(ConductorState)
    graph.add_node("model", model)
    graph.add_edge(START, "model")
    graph.add_edge("model", END)
    agent = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "same-thread"}}
    state = initial_probe_state(("app/page",))
    with pytest.raises(ConnectionError):
        agent.invoke({"messages": [HumanMessage("start")], **state}, config)
    resumed = agent.invoke(None, config)
    assert resumed['probe']['run_id'] == state['probe']['run_id']
    continued = agent.invoke({"messages": [HumanMessage("continue")], **reset_collection_state()}, config)
    assert continued['probe']['run_id'] == state['probe']['run_id']
    assert observed == [state['probe']['run_id']] * 3
    fresh = initial_probe_state(("app/page",))
    assert fresh['probe']['run_id'] != state['probe']['run_id']
    assert agent.invoke(fresh, config)['probe']['run_id'] == fresh['probe']['run_id']
    other = {"configurable": {"thread_id": "other-thread"}}
    other_state = initial_probe_state(("app/page",))
    assert agent.invoke(other_state, other)['probe']['run_id'] == other_state['probe']['run_id']
    assert agent.get_state(config).values['probe']['run_id'] == fresh['probe']['run_id']
