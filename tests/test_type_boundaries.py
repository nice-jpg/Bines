
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from pydantic import ValidationError

from src.agent_messages.text import latest_message_text
from src.common.utils.json import json_value
from src.conductor.state import read_conductor_fields, read_conductor_state
from src.device.types.page import artifact_snapshot
from src.strategy.tool_io import with_state_update


def test_state_validation_preserves_checkpoint_and_message_identity():
    message = AIMessage(content="ready")
    extension = object()
    checkpoint = {"messages": [message], "general": {"visit": {"path": "app/page", "visit_id": "visit", "strategy": "general"}}, "extension": extension}
    state = read_conductor_state(checkpoint)
    assert state["messages"][0] is message
    assert state["general"]["visit"]["path"] == "app/page"
    assert checkpoint["extension"] is extension
    assert read_conductor_fields(checkpoint)["general"] == state["general"]


def test_current_checkpoint_preserves_strategy_and_collection_evidence() -> None:
    from page_strategy_helpers import page_fields

    checkpoint = read_conductor_state(page_fields("app/shop", "visit", "merchant"))
    checkpoint["merchant"].update(phase="completed", training_version="v1", entry_taps=1)
    checkpoint["merchant"]["evidence"].update(
        initialized=True, latest_new_count=0, consecutive_empty_swipes=3, last_swipe="swipe_up"
    )
    checkpoint["probe"] = {"run_id": "run", "target_leaf_paths": ["app/shop"]}
    restored = read_conductor_state(checkpoint)
    assert restored == checkpoint
    assert read_conductor_fields(restored) == {
        key: value for key, value in checkpoint.items() if key != "messages"
    }



@pytest.mark.parametrize("value", ["1", True, None])
def test_collection_counters_require_integers(value):
    with pytest.raises(ValidationError):
        read_conductor_fields({"merchant": {"evidence": {"latest_new_count": value}}})


def test_artifact_snapshot_is_validated_and_detached():
    raw = {"xml": "<root/>", "aliases": {"1": [2]}, "snapshot_id": "s", "captured_at": "now"}
    snapshot = artifact_snapshot({"page_snapshot": raw})
    assert snapshot == raw
    raw["aliases"]["1"].append(3)
    assert snapshot["aliases"]["1"] == [2]
    with pytest.raises(ValidationError):
        artifact_snapshot({"page_snapshot": {**raw, "xml": 7}})


def test_command_merge_retains_framework_extensions():
    extension = object()
    message = ToolMessage(content="ok", tool_call_id="call")
    result = with_state_update(
        Command(graph=Command.PARENT, update={"messages": [message], "extension": extension},
                resume="continue", goto="next"),
        {"general": {"visit": {"visit_id": "visit", "path": "app/page", "strategy": "general"}}},
    )
    assert result.update["extension"] is extension
    assert result.update["messages"][0] is message
    assert result.update['general']['visit']['visit_id'] == "visit"
    assert (result.graph, result.resume, result.goto) == (Command.PARENT, "continue", "next")


def test_message_boundary_accepts_framework_and_serialized_text():
    assert latest_message_text({"messages": [AIMessage(content="ready")]}) == "ready"
    assert latest_message_text({"messages": [{"content": ""}]}) == ""
    with pytest.raises(TypeError):
        latest_message_text({"messages": [{"content": 42}]})


def test_json_boundary_rejects_non_json_keys_and_objects():
    with pytest.raises(TypeError):
        json_value({1: "value"})
    with pytest.raises(TypeError):
        json_value({"value": object()})
