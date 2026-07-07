from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collector import CollectorManager, create_collector_tools
from page_mechanism import PageMechanism


class FakeEventHub:
    def __init__(self) -> None:
        self.calls = []
        self.xml = "<h><n t=\"cup\"/></h>"

    def uiautomate(self):
        self.calls.append(("uiautomate",))
        return self.xml

    def swipe_up(self, x, y):
        self.calls.append(("swipe_up", x, y))
        return self.xml

    def swipe_down(self, x, y):
        self.calls.append(("swipe_down", x, y))
        return self.xml


def test_collector_tools_exclude_click_and_back_navigation(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.py").write_text("def run(xml: str, previous=None):\n    return {'items': []}\n", encoding="utf-8")

    tools = create_collector_tools(
        mechanism=PageMechanism(tmp_path),
        event_hub=FakeEventHub(),
        page_path="meituan",
    )
    names = [tool.name for tool in tools]

    assert names == [
        "collector_uiautomate",
        "collector_swipe_up",
        "collector_swipe_down",
        "read_run_py",
        "write_run_py",
        "sniff_run",
        "eval_collection",
    ]
    assert "tap" not in names
    assert "swipe_back" not in names


def test_collector_sniff_run_executes_current_page_script(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.py").write_text("def run(xml: str, previous=None):\n    return {'items': [{'name': 'cup'}]}\n", encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in create_collector_tools(
            mechanism=PageMechanism(tmp_path),
            event_hub=FakeEventHub(),
            page_path="meituan",
        )
    }

    result = json.loads(tools["sniff_run"].func())

    assert result["ok"] is True
    assert result["result"] == {"items": [{"name": "cup"}]}


def test_prepare_collector_invokes_isolated_collector_agent() -> None:
    captured = {}

    class FakeAgent:
        def invoke(self, payload, config=None):
            captured["payload"] = payload
            captured["config"] = config
            return {"messages": [{"role": "assistant", "content": "score=100"}]}

    def create_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeAgent()

    manager = CollectorManager(model="model", event_hub=FakeEventHub(), create_agent_factory=create_agent)
    result = json.loads(
        manager.prepare_collector(
            page_path="meituan",
            page_description="home page",
            required_fields="name, price",
            output_schema='{"items": [{"name": "", "price": 0}]}',
        )
    )

    assert result["ok"] is True
    assert result["result"] == "score=100"
    assert captured["kwargs"]["name"] == "collector"
    assert captured["config"]["recursion_limit"] == 80
    assert "home page" in captured["payload"]["messages"][0]["content"]
