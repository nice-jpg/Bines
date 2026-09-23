"""Reco contract and model-visible/internal snapshot boundaries."""

import json

import pytest
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage
from Reco.model_interface import TOOLS
from src.tools.page_bridge import PageBridge
from src.tools.event_hub import EventHub, create_conductor_event_hub_tools


XML = '<hierarchy><node class="android.widget.Button" text="商品" clickable="true" bounds="[0,0][100,80]" resource-id="private-id"/></hierarchy>'


def test_bridge_tools_match_reco_schema_and_do_not_leak_xml():
    bridge = PageBridge(lambda: XML)
    tools = {tool.name: tool for tool in bridge.create_tools()}
    assert {name: tool.args_schema for name, tool in tools.items()} == {
        spec["name"]: spec["input_schema"] for spec in TOOLS
    }
    result = tools["page_view"].invoke({"type": "tool_call", "id": "view-1", "name": "page_view", "args": {}})
    assert json.loads(result.content)["summary"] == "商品"
    assert "private-id" not in result.content
    assert "<hierarchy" in result.artifact["page_xml"]
    model = ChatOpenAI(model="test", api_key="test")
    payload = model._get_request_payload([
        AIMessage(content="", tool_calls=[{"id": "view-1", "name": "page_view", "args": {}}]), result
    ])
    assert "private-id" not in json.dumps(payload)
    assert "page_xml" not in json.dumps(payload)


def test_bridge_reads_one_snapshot_and_syncs_ids_and_aliases():
    current = [XML]
    dumps = []
    bridge = PageBridge(lambda: dumps.append(1) or current[0])
    view, _ = bridge.call("page_view", {})
    key = view["regions"][0]["id"]
    bridge.call("update_aaid", {"id": key, "value": "12"})
    node, _ = bridge.call("select_aaid", {"aaid": 12})
    assert node["id"] == key
    assert node["bounds"] == [0, 0, 100, 80]
    exact, _ = bridge.call("page_read", {"key": key, "max_chars": 1})
    assert exact["next"] is not None
    bridge.call("page_catalog", {})
    bridge.call("page_view", {"key": key})
    assert len(dumps) == 1
    current[0] = XML.replace("商品", "点菜")
    bridge.call("page_view", {})
    node, _ = bridge.call("select_aaid", {"aaid": 12})
    assert node["id"] == key
    ratio, _ = bridge.call("update_ratio", {"id": key})
    assert ratio > 0
    with pytest.raises(ValueError):
        bridge.call("select_aaid", {"aaid": "12"})


def test_invalid_capture_clears_stale_snapshot():
    current = [XML]
    bridge = PageBridge(lambda: current[0])
    bridge.call("page_view", {})
    current[0] = "invalid xml"
    error, artifact = bridge.call("page_view", {})
    assert error["ok"] is False
    assert artifact is None
    assert bridge.xml == ""


def test_reco_evidence_and_script_snapshot_keep_the_same_nodes():
    xml = '<hierarchy><node text="visible"/><node text="hidden evidence" visible-to-user="false"/></hierarchy>'
    bridge = PageBridge(lambda: xml)
    bridge.call("page_view", {})
    evidence, artifact = bridge.call("page_read", {})
    assert "hidden evidence" in json.dumps(evidence)
    assert artifact["page_xml"] == xml


def test_device_tool_returns_abstract_content_and_hidden_snapshot():
    hub = EventHub(sleep_func=lambda _: None)
    hub._actions_checked = True
    hub.device.act = lambda **kwargs: ""
    hub.device.dump_ui = lambda: XML
    tools = {tool.name: tool for tool in create_conductor_event_hub_tools(hub)}
    assert "uiautomate" not in tools
    result = tools["swipe_up"].invoke({
        "type": "tool_call", "id": "swipe-1", "name": "swipe_up", "args": {"x": 50, "y": 500}
    })
    assert "<hierarchy" not in result.content
    assert json.loads(result.content)["summary"] == "商品"
    assert result.artifact["page_xml"] == hub.page_bridge.xml
