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
    (page_dir / "run.py").write_text("def run(xml: str):\n    return {'items': []}\n", encoding="utf-8")

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
        "save_baseline",
    ]
    assert "tap" not in names
    assert "swipe_back" not in names
    assert "eval_collection" not in names


def test_collector_sniff_run_executes_current_page_script(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.py").write_text("def run(xml: str):\n    return {'items': [{'name': 'cup'}]}\n", encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in create_collector_tools(
            mechanism=PageMechanism(tmp_path),
            event_hub=FakeEventHub(),
            page_path="meituan",
        )
    }

    tools["save_baseline"].func('{"items": [{"name": "cup"}]}')
    result = json.loads(tools["sniff_run"].func())

    assert result["ok"] is True
    assert result["run"]["ok"] is True
    assert result["run"]["result"] == {"items": [{"name": "cup"}]}
    assert result["evaluation"] == {"score": 100, "errors": [], "missing": []}


def test_sniff_run_uses_saved_baseline_and_replacement(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.py").write_text(
        "def run(xml: str):\n"
        "    return {'items': [{'name': 'cup'}]}\n",
        encoding="utf-8",
    )
    tools = {
        tool.name: tool
        for tool in create_collector_tools(
            mechanism=PageMechanism(tmp_path),
            event_hub=FakeEventHub(),
            page_path="meituan",
        )
    }

    missing = json.loads(tools["sniff_run"].func())
    saved = json.loads(tools["save_baseline"].func('{"items": [{"name": "cup"}]}'))
    matched = json.loads(tools["sniff_run"].func())
    tools["save_baseline"].func('{"items": [{"name": "bowl"}]}')
    replaced = json.loads(tools["sniff_run"].func())

    assert missing["evaluation"]["score"] == 0
    assert "missing_baseline" in missing["evaluation"]["errors"]
    assert saved["baseline_saved"] is True
    assert matched["evaluation"]["score"] == 100
    assert replaced["evaluation"]["score"] < 100
    assert replaced["evaluation"]["errors"] == ["result.items[0].name"]


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
