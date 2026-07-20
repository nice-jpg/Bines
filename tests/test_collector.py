from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collector import CollectorManager, CollectorToolbox, create_collector_tools
from device.results import make_error_result
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
    (page_dir / "run.agent.py").write_text("def run(xml: str):\n    return {'items': []}\n", encoding="utf-8")

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
        "read_run_agent_py",
        "apply_run_agent_py_patch",
        "sniff_run",
        "save_baseline",
        "save_merchant_profile",
    ]
    assert "tap" not in names
    assert "swipe_back" not in names
    assert "eval_collection" not in names


def test_run_agent_py_reads_are_deduplicated_and_external_changes_refresh_content(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.agent.py"
    original = "def run(xml: str):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=FakeEventHub(),
        page_path="meituan",
    )

    first = json.loads(toolbox.read_run_agent_py())
    repeated = json.loads(toolbox.read_run_agent_py())
    applied = json.loads(
        toolbox.apply_run_agent_py_patch(
            (
                "*** Begin Patch\n*** Update File: run.agent.py\n@@\n"
                "-    return {'items': []}\n+    return {'items': [xml]}\n*** End Patch"
            )
        )
    )
    after_patch = json.loads(toolbox.read_run_agent_py())
    external = "def run(xml: str):\n    return {'items': [{'external': xml}]}\n"
    run_path.write_text(external, encoding="utf-8")
    after_external_change = json.loads(toolbox.read_run_agent_py())

    assert first["content"] == original
    assert first["content_omitted"] is False
    assert repeated["content_omitted"] is True
    assert "content" not in repeated
    assert applied["ok"] is True
    assert "content" not in applied
    assert after_patch["content_omitted"] is True
    assert "content" not in after_patch
    assert after_external_change["content_omitted"] is False
    assert after_external_change["content"] == external


def test_read_run_agent_py_reports_missing_script_without_fake_content(tmp_path: Path) -> None:
    (tmp_path / "meituan").mkdir()
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=FakeEventHub(),
        page_path="meituan",
    )

    result = json.loads(toolbox.read_run_agent_py())

    assert result["ok"] is False
    assert result["error_type"] == "MissingRunAgentPy"
    assert "content" not in result


def test_collector_sniff_run_executes_current_page_script(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "PAGE.md").write_text("home", encoding="utf-8")
    (page_dir / "task.md").write_text("task", encoding="utf-8")
    (page_dir / "run.agent.py").write_text("def run(xml: str):\n    return {'items': [{'name': 'cup'}]}\n", encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in create_collector_tools(
            mechanism=PageMechanism(tmp_path),
            event_hub=FakeEventHub(),
            page_path="meituan",
        )
    }

    tools["collector_uiautomate"].func()
    tools["save_baseline"].func('{"items": [{"name": "cup"}]}')
    result = json.loads(tools["sniff_run"].func())

    assert result["ok"] is True
    assert result["run"]["ok"] is True
    assert result["run"]["result"] == {"items": [{"name": "cup"}]}
    assert result["evaluation"] == {"score": 100, "errors": [], "missing": []}
    assert result["verification"]["ready"] is False
    assert result["verification"]["validated_states"] == 1


def test_sniff_run_uses_saved_baseline_and_replacement(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "PAGE.md").write_text("home", encoding="utf-8")
    (page_dir / "task.md").write_text("task", encoding="utf-8")
    (page_dir / "run.agent.py").write_text(
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

    tools["collector_uiautomate"].func()
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


def test_sniff_run_rejects_baseline_from_another_xml_state(tmp_path: Path) -> None:
    class MovingEventHub(FakeEventHub):
        def swipe_up(self, x, y):
            self.xml = '<h><n t="bowl"/></h>'
            return super().swipe_up(x, y)

    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text("def run(xml: str):\n    return {'items': []}\n", encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in create_collector_tools(
            mechanism=PageMechanism(tmp_path),
            event_hub=MovingEventHub(),
            page_path="meituan",
        )
    }

    tools["collector_uiautomate"].func()
    tools["save_baseline"].func('{"items": []}')
    tools["collector_swipe_up"].func(100, 1000)
    result = json.loads(tools["sniff_run"].func())

    assert result["ok"] is False
    assert result["evaluation"]["errors"] == ["baseline_state_mismatch"]


def test_same_script_must_pass_two_distinct_swiped_states(tmp_path: Path) -> None:
    class MovingEventHub(FakeEventHub):
        def swipe_up(self, x, y):
            self.xml = '<h><n t="bowl"/></h>'
            return super().swipe_up(x, y)

    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "PAGE.md").write_text("home", encoding="utf-8")
    (page_dir / "task.md").write_text("task", encoding="utf-8")
    (page_dir / "run.agent.py").write_text(
        "from xml.etree import ElementTree as ET\n"
        "def run(xml: str):\n"
        "    name = ET.fromstring(xml).find('n').attrib['t']\n"
        "    return {'items': [{'name': name}]}\n",
        encoding="utf-8",
    )
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=MovingEventHub(),
        page_path="meituan",
    )

    toolbox.collector_uiautomate()
    toolbox.save_baseline('{"items": [{"name": "cup"}]}')
    first = json.loads(toolbox.sniff_run())
    toolbox.collector_swipe_up(100, 1000)
    toolbox.save_baseline('{"items": [{"name": "bowl"}]}')
    second = json.loads(toolbox.sniff_run())

    assert first["evaluation"]["score"] == 100
    assert first["verification"]["ready"] is False
    assert second["evaluation"]["score"] == 100
    assert second["verification"]["ready"] is True
    assert second["verification"]["validated_states"] == 2

    patched = json.loads(
        toolbox.apply_run_agent_py_patch(
            (
                "*** Begin Patch\n*** Update File: run.agent.py\n@@\n"
                " from xml.etree import ElementTree as ET\n"
                "+# Script version changed without changing extraction behavior.\n"
                " def run(xml: str):\n"
                "*** End Patch"
            )
        )
    )
    invalidated = toolbox.training_result()
    assert patched["ok"] is True
    assert invalidated["ready"] is False
    assert invalidated["evaluation"]["errors"] == ["script_changed_since_evaluation"]


def test_hardcoded_current_state_script_fails_cross_state_verification(tmp_path: Path) -> None:
    class MovingEventHub(FakeEventHub):
        def swipe_up(self, x, y):
            self.xml = '<h><n t="bowl"/></h>'
            return super().swipe_up(x, y)

    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text(
        "def run(xml: str):\n    return {'items': [{'name': 'cup'}]}\n",
        encoding="utf-8",
    )
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=MovingEventHub(),
        page_path="meituan",
    )

    toolbox.collector_uiautomate()
    toolbox.save_baseline('{"items": [{"name": "cup"}]}')
    first = json.loads(toolbox.sniff_run())
    toolbox.collector_swipe_up(100, 1000)
    toolbox.save_baseline('{"items": [{"name": "bowl"}]}')
    second = json.loads(toolbox.sniff_run())

    assert first["evaluation"]["score"] == 100
    assert second["evaluation"]["score"] < 100
    assert second["evaluation"]["errors"] == ["result.items[0].name"]
    assert toolbox.training_result()["ready"] is False


def test_sniff_run_reports_device_error_without_running_page_script(tmp_path: Path) -> None:
    class ErrorEventHub(FakeEventHub):
        def uiautomate(self):
            return make_error_result("command_error", "adb failed")

    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text("raise AssertionError('must not load')\n", encoding="utf-8")
    tools = {
        tool.name: tool
        for tool in create_collector_tools(
            mechanism=PageMechanism(tmp_path),
            event_hub=ErrorEventHub(),
            page_path="meituan",
        )
    }

    result = json.loads(tools["sniff_run"].func())

    assert result["ok"] is False
    assert result["run"]["error_type"] == "command_error"
    assert result["evaluation"] == {
        "score": 0,
        "errors": ["uiautomate_error"],
        "missing": ["result"],
    }


def test_prepare_collector_uses_tool_verification_not_final_ai_text(tmp_path: Path) -> None:
    captured = {}

    class MovingEventHub(FakeEventHub):
        def swipe_up(self, x, y):
            self.xml = '<h><n t="bowl"/></h>'
            return super().swipe_up(x, y)

    class FakeAgent:
        def __init__(self, tools) -> None:
            self.tools = {tool.name: tool for tool in tools}

        def invoke(self, payload, config=None):
            captured["payload"] = payload
            captured["config"] = config
            self.tools["collector_uiautomate"].func()
            self.tools["save_baseline"].func('{"items": [{"name": "cup"}]}')
            self.tools["sniff_run"].func()
            self.tools["collector_swipe_up"].func(100, 1000)
            self.tools["save_baseline"].func('{"items": [{"name": "bowl"}]}')
            self.tools["sniff_run"].func()
            return {"messages": [{"role": "assistant", "content": "Ready. Score 100."}]}

    def create_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeAgent(kwargs["tools"])

    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "PAGE.md").write_text("home", encoding="utf-8")
    (page_dir / "task.md").write_text("task", encoding="utf-8")
    (page_dir / "run.agent.py").write_text(
        "from xml.etree import ElementTree as ET\n"
        "def run(xml: str):\n"
        "    name = ET.fromstring(xml).find('n').attrib['t']\n"
        "    return {'items': [{'name': name}]}\n",
        encoding="utf-8",
    )

    manager = CollectorManager(
        model="model",
        mechanism=PageMechanism(tmp_path),
        event_hub=MovingEventHub(),
        create_agent_factory=create_agent,
    )
    result = json.loads(
        manager.prepare_collector(
            page_path="meituan",
            page_description="home page",
            merchant_schema="{}",
            collection_schema='{"items": [{"name": ""}]}',
            runtime=SimpleNamespace(
                state={"page_context_path": "meituan", "page_visit_id": "visit-home"}
            ),
        )
    )

    assert result["ok"] is True
    assert result["collector_message"] == "Ready. Score 100."
    assert result["trained"] is True
    assert result["verification"]["ready"] is True
    assert result["verification"]["validated_states"] == 2
    assert manager.training_registry.is_trained("visit-home", "meituan") is True
    assert result["restoration"]["restored"] is True
    assert captured["kwargs"]["name"] == "collector"
    assert captured["config"]["recursion_limit"] == 80
    assert "home page" in captured["payload"]["messages"][0]["content"]


def test_prepare_collector_final_ai_claim_cannot_mark_script_trained() -> None:
    class FakeAgent:
        def __init__(self, content: str) -> None:
            self.content = content

        def invoke(self, payload, config=None):
            return {"messages": [{"role": "assistant", "content": self.content}]}

    for content in ['{"ready": true, "score": 100}', "Ready. Score 100.", "not json"]:
        def create_agent(**kwargs):
            return FakeAgent(content)

        manager = CollectorManager(
            model="model",
            event_hub=FakeEventHub(),
            create_agent_factory=create_agent,
        )
        result = json.loads(
            manager.prepare_collector(
                page_path="meituan/美食/商家",
                page_description="merchant",
                merchant_schema='{"name": ""}',
                collection_schema='{"items": []}',
                runtime=SimpleNamespace(
                    state={
                        "page_context_path": "meituan/美食/商家",
                        "page_visit_id": f"visit-{content}",
                    }
                ),
            )
        )

        assert result["trained"] is False
        assert result["verification"]["ready"] is False
        assert manager.training_registry.is_trained(f"visit-{content}", "meituan/美食/商家") is False


def test_merchant_profile_is_required_but_not_part_of_item_scoring(tmp_path: Path) -> None:
    class MovingEventHub(FakeEventHub):
        def swipe_up(self, x, y):
            self.xml = '<h><n t="bowl"/></h>'
            return super().swipe_up(x, y)

    page_dir = tmp_path / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.agent.py").write_text(
        "from xml.etree import ElementTree as ET\n"
        "def run(xml: str):\n"
        "    return {'items': [{'name': ET.fromstring(xml).find('n').attrib['t']}]}\n",
        encoding="utf-8",
    )
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=MovingEventHub(),
        page_path="meituan/美食/商家",
        merchant_schema='{"name": "", "distance": ""}',
        collection_schema='{"items": [{"name": ""}]}',
        requires_merchant_profile=True,
    )

    toolbox.collector_uiautomate()
    invalid = json.loads(toolbox.save_merchant_profile('{"name": "", "distance": "1km"}'))
    saved = json.loads(toolbox.save_merchant_profile('{"name": "商家A", "distance": "1km"}'))
    toolbox.save_baseline('{"items": [{"name": "cup"}]}')
    first = json.loads(toolbox.sniff_run())
    toolbox.collector_swipe_up(100, 1000)
    toolbox.save_baseline('{"items": [{"name": "bowl"}]}')
    second = json.loads(toolbox.sniff_run())

    assert invalid["saved"] is False
    assert invalid["errors"] == ["merchant.name"]
    assert saved["saved"] is True
    assert first["evaluation"]["score"] == 100
    assert second["evaluation"]["score"] == 100
    assert second["verification"]["ready"] is True


def test_restore_to_top_uses_down_swipes_without_changing_verification(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text("def run(xml: str):\n    return {'items': []}\n", encoding="utf-8")
    event_hub = FakeEventHub()
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=event_hub,
        page_path="meituan",
    )
    before = toolbox.training_result()

    restoration = toolbox.restore_to_top()

    assert restoration["restored"] is True
    assert restoration["attempts"] >= 3
    assert [call[0] for call in event_hub.calls] == ["swipe_down"] * restoration["attempts"]
    assert toolbox.training_result() == before


def test_merchant_profile_cannot_be_saved_after_scrolling(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.agent.py").write_text("def run(xml: str):\n    return {'items': []}\n", encoding="utf-8")
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=FakeEventHub(),
        page_path="meituan/美食/商家",
        merchant_schema='{"name": ""}',
        collection_schema='{"items": []}',
        requires_merchant_profile=True,
    )

    toolbox.collector_uiautomate()
    toolbox.collector_swipe_up(100, 1000)
    result = json.loads(toolbox.save_merchant_profile('{"name": "商家A"}'))

    assert result["saved"] is False
    assert result["error_type"] == "MerchantProfileStateError"


def test_prepare_collector_rejects_stale_page_visit_before_building_agent(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "PAGE.md").write_text("home", encoding="utf-8")
    (page_dir / "task.md").write_text("task", encoding="utf-8")
    manager = CollectorManager(model="model", mechanism=PageMechanism(tmp_path), event_hub=FakeEventHub())

    try:
        manager.prepare_collector(
            page_path="meituan",
            page_description="home",
            merchant_schema="{}",
            collection_schema='{"items": []}',
            runtime=SimpleNamespace(
                state={"page_context_path": "meituan/外卖", "page_visit_id": "visit-stale"}
            ),
        )
    except ValueError as exc:
        assert "does not match current page" in str(exc)
    else:
        raise AssertionError("stale collector assignment must be rejected")


def test_restore_failure_prevents_training_completion(tmp_path: Path) -> None:
    class RestoreErrorEventHub(FakeEventHub):
        def swipe_up(self, x, y):
            self.xml = '<h><n t="bowl"/></h>'
            return super().swipe_up(x, y)

        def swipe_down(self, x, y):
            self.calls.append(("swipe_down", x, y))
            return make_error_result("command_error", "cannot restore")

    class FakeAgent:
        def __init__(self, tools) -> None:
            self.tools = {tool.name: tool for tool in tools}

        def invoke(self, payload, config=None):
            self.tools["collector_uiautomate"].func()
            self.tools["save_baseline"].func('{"items": [{"name": "cup"}]}')
            self.tools["sniff_run"].func()
            self.tools["collector_swipe_up"].func(100, 1000)
            self.tools["save_baseline"].func('{"items": [{"name": "bowl"}]}')
            self.tools["sniff_run"].func()
            return {"messages": [{"role": "assistant", "content": "ready"}]}

    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "PAGE.md").write_text("home", encoding="utf-8")
    (page_dir / "task.md").write_text("task", encoding="utf-8")
    (page_dir / "run.agent.py").write_text(
        "from xml.etree import ElementTree as ET\n"
        "def run(xml: str):\n"
        "    return {'items': [{'name': ET.fromstring(xml).find('n').attrib['t']}]}\n",
        encoding="utf-8",
    )
    manager = CollectorManager(
        model="model",
        mechanism=PageMechanism(tmp_path),
        event_hub=RestoreErrorEventHub(),
        create_agent_factory=lambda **kwargs: FakeAgent(kwargs["tools"]),
    )

    result = json.loads(
        manager.prepare_collector(
            page_path="meituan",
            page_description="home",
            merchant_schema="{}",
            collection_schema='{"items": [{"name": ""}]}',
            runtime=SimpleNamespace(
                state={"page_context_path": "meituan", "page_visit_id": "visit-restore-error"}
            ),
        )
    )

    assert result["verification"]["ready"] is True
    assert result["restoration"]["restored"] is False
    assert result["trained"] is False
    assert manager.training_registry.is_trained("visit-restore-error", "meituan") is False
