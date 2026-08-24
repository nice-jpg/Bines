from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from collector import (
    CollectorCoderSession,
    CollectorCoderToolbox,
    CollectorManager,
    CollectorSavedInstanceMap,
    CollectorToolbox,
    create_collector_tools,
)
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

    def swipe_up_long(self, x, y):
        self.calls.append(("swipe_up_long", x, y))
        return self.xml

    def swipe_to_random(self, x, y):
        self.calls.append(("swipe_to_random", x, y))
        return self.xml

    def swipe_down(self, x, y):
        self.calls.append(("swipe_down", x, y))
        return self.xml

    def tap(self, x, y):
        self.calls.append(("tap", x, y))
        return self.xml


def test_collector_tools_exclude_click_and_back_navigation(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text("def run(xml: str):\n    return {'items': []}\n", encoding="utf-8")

    event_hub = FakeEventHub()
    tools = create_collector_tools(
        mechanism=PageMechanism(tmp_path),
        event_hub=event_hub,
        page_path="meituan",
    )
    names = [tool.name for tool in tools]

    assert names == [
        "collector_uiautomate",
        "collector_swipe_up",
        "collector_swipe_up_long",
        "collector_swipe_to_random",
        "collector_swipe_down",
        "collector_tap",
        "consult_coder",
        "sniff_run",
        "save_baseline",
        "save_merchant_profile",
    ]
    assert "tap" not in names
    assert "swipe_back" not in names
    assert "eval_collection" not in names

    by_name = {tool.name: tool for tool in tools}
    by_name["collector_swipe_up_long"].invoke({"x": 100, "y": 1800})
    by_name["collector_swipe_to_random"].invoke({"x": 200, "y": 1600})
    assert event_hub.calls == [
        ("swipe_up_long", 100, 1800),
        ("swipe_to_random", 200, 1600),
    ]


def test_run_agent_py_reads_are_deduplicated_and_external_changes_refresh_content(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.agent.py"
    original = "def run(xml: str):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")
    toolbox = CollectorCoderToolbox(
        mechanism=PageMechanism(tmp_path),
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
    toolbox = CollectorCoderToolbox(
        mechanism=PageMechanism(tmp_path),
        page_path="meituan",
    )

    result = json.loads(toolbox.read_run_agent_py())

    assert result["ok"] is False
    assert result["error_type"] == "MissingRunAgentPy"
    assert "content" not in result


def test_collector_coder_exposes_only_page_script_file_tools(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text(
        "def run(xml: str):\n    return {'items': []}\n",
        encoding="utf-8",
    )

    toolbox = CollectorCoderToolbox(
        mechanism=PageMechanism(tmp_path),
        page_path="meituan",
    )

    assert [tool.name for tool in toolbox.create_tools()] == [
        "read_run_agent_py",
        "apply_run_agent_py_patch",
    ]


def test_collector_coder_reuses_agent_thread_and_sends_only_new_cases(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text(
        "def run(xml: str):\n    return {'items': []}\n",
        encoding="utf-8",
    )
    built = []
    invocations = []

    class FakeCoderAgent:
        def invoke(self, payload, config=None):
            invocations.append((payload, config))
            return {
                "messages": [
                    *payload["messages"],
                    {"role": "assistant", "content": "patched"},
                ]
            }

    def create_agent(**kwargs):
        built.append(kwargs)
        return FakeCoderAgent()

    session = CollectorCoderSession(
        model="model",
        mechanism=PageMechanism(tmp_path),
        page_path="meituan",
        page_visit_id="visit-1",
        page_description="merchant page",
        collection_schema={"items": [{"name": ""}]},
        create_agent_factory=create_agent,
    )
    first_case = {
        "case_id": "case-1",
        "xml": "<page><item text='cup'/></page>",
        "r1": {"items": [{"name": "cup"}]},
    }
    second_case = {
        "case_id": "case-2",
        "xml": "<page><item text='bowl'/></page>",
        "r1": {"items": [{"name": "bowl"}]},
    }

    first = json.loads(
        session.implement(training_cases=[first_case], evaluation={"score": 0})
    )
    second = json.loads(
        session.implement(
            training_cases=[first_case, second_case],
            evaluation={"score": 50, "errors": ["cases[1].result.items[0].name"]},
            instruction=(
                "The second repeated item container has the same width and child topology as the first, "
                "but its label is split across sibling nodes; locate the repeated container structurally."
            ),
        )
    )

    assert len(built) == 1
    assert [tool.name for tool in built[0]["tools"]] == [
        "read_run_agent_py",
        "apply_run_agent_py_patch",
    ]
    assert "checkpointer" not in built[0]
    assert first["turn"] == 1
    assert second["turn"] == 2
    assert first["history_messages"] == 2
    assert second["history_messages"] == 4
    assert first["new_training_cases"] == 1
    assert second["new_training_cases"] == 1
    first_payload = json.loads(invocations[0][0]["messages"][0]["content"])
    second_payload = json.loads(invocations[1][0]["messages"][-1]["content"])
    assert invocations[1][0]["messages"][:2] == invocations[0][0]["messages"] + [
        {"role": "assistant", "content": "patched"}
    ]
    assert first_payload["new_training_cases"] == [first_case]
    assert second_payload["new_training_cases"] == [second_case]
    assert second_payload["template_parent_path"] == "meituan"
    assert second_payload["page_description"] is None
    assert second_payload["problem_report"]["score"] == 50
    assert second_payload["problem_report"]["failed_paths"] == [
        "cases[1].result.items[0].name"
    ]
    assert "same width and child topology" in second_payload["problem_report"]["structural_analysis"]
    assert "do not turn literal sample text" in second_payload["problem_report"]["analysis_scope"]
    assert invocations[0][1]["configurable"]["thread_id"] == "collector-coder:visit-1"
    assert invocations[1][1]["configurable"]["thread_id"] == "collector-coder:visit-1"
    assert session.message_history() == tuple(
        [
            *invocations[1][0]["messages"],
            {"role": "assistant", "content": "patched"},
        ]
    )


def test_collector_consult_coder_supplies_accumulated_pairs_and_batch_result(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text(
        "def run(xml: str):\n    return {'items': []}\n",
        encoding="utf-8",
    )
    received = []

    class FakeCoderSession:
        def implement(self, **kwargs):
            received.append(kwargs)
            return json.dumps({"ok": True})

    event_hub = FakeEventHub()
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=event_hub,
        page_path="meituan",
        collection_schema='{"items": [{"name": ""}]}',
        coder_session=FakeCoderSession(),
    )
    toolbox.collector_uiautomate()
    toolbox.save_baseline('{"items": [{"name": "cup"}]}')
    event_hub.xml = "<h><n t=\"bowl\"/></h>"
    toolbox.collector_swipe_up(100, 1000)
    toolbox.save_baseline('{"items": [{"name": "bowl"}]}')
    toolbox.sniff_run()

    rejected = json.loads(toolbox.consult_coder("handle repeated card variants"))
    diagnosis = (
        "Both states use repeated item containers with the same width and child hierarchy, but labels may be split "
        "across sibling nodes. The current script returns no cards, so identify the shared container structurally."
    )
    result = json.loads(toolbox.consult_coder(diagnosis))

    assert rejected["error_type"] == "CollectorCoderDiagnosisInsufficient"
    assert rejected["minimum_characters"] == 60
    assert result["ok"] is True
    assert len(received) == 1
    assert len(received[0]["training_cases"]) == 2
    assert received[0]["training_cases"][1]["xml"] == event_hub.xml
    assert received[0]["training_cases"][0]["r1"] == {"items": [{"name": "cup"}]}
    assert received[0]["evaluation"]["score"] < 100
    assert received[0]["instruction"] == diagnosis
    assert [call[0] for call in event_hub.calls].count("uiautomate") == 2


def test_collector_saved_instances_require_coder_to_cover_every_visit_for_path(
    tmp_path: Path,
) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text(
        "def run(xml: str):\n    return {'items': [{'name': 'new'}]}\n",
        encoding="utf-8",
    )
    saved_instances = CollectorSavedInstanceMap()

    first_hub = FakeEventHub()
    first = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=first_hub,
        page_path="meituan",
        collection_schema='{"items": [{"name": ""}]}',
        saved_instances=saved_instances,
        page_visit_id="visit-a",
    )
    first.collector_uiautomate()
    first.save_baseline('{"items": [{"name": "old-a"}]}')
    first_hub.xml = '<h state="second"><n t="old-b"/></h>'
    first.collector_swipe_up(100, 1000)
    first.save_baseline('{"items": [{"name": "old-b"}]}')

    received = []

    class FakeCoderSession:
        def implement(self, **kwargs):
            received.append(kwargs)
            return json.dumps({"ok": True})

    second_hub = FakeEventHub()
    second_hub.xml = '<h merchant="new" state="first"/>'
    second = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=second_hub,
        page_path="meituan",
        collection_schema='{"items": [{"name": ""}]}',
        coder_session=FakeCoderSession(),
        saved_instances=saved_instances,
        page_visit_id="visit-b",
    )
    second.collector_uiautomate()
    second.save_baseline('{"items": [{"name": "new"}]}')
    second_hub.xml = '<h merchant="new" state="second"/>'
    second.collector_swipe_up(100, 1000)
    second.save_baseline('{"items": [{"name": "new"}]}')

    sniffed = json.loads(second.sniff_run())
    diagnosis = (
        "The script matches the current visit but fails earlier instances of the shared template. "
        "Use repeated container hierarchy and field placement to recognize the business value across variants."
    )
    consulted = json.loads(second.consult_coder(diagnosis))

    instances = saved_instances.instances_for_path("meituan")
    assert [instance["page_visit_id"] for instance in instances] == ["visit-a", "visit-b"]
    assert [len(instance["cases"]) for instance in instances] == [2, 2]
    assert sniffed["evaluated_cases"] == 4
    assert sniffed["evaluation"]["score"] < 100
    assert sniffed["verification"]["ready"] is False
    assert sniffed["verification"]["saved_instances"] == 2
    assert sniffed["verification"]["corpus_cases"] == 4
    assert sniffed["verification"]["validated_corpus_cases"] == 2
    assert consulted["ok"] is True
    assert len(received) == 1
    assert {case["page_visit_id"] for case in received[0]["training_cases"]} == {
        "visit-a",
        "visit-b",
    }
    assert len(received[0]["training_cases"]) == 4


def test_collector_skips_coder_when_existing_script_already_scores_100(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text(
        "from xml.etree import ElementTree as ET\n"
        "def run(xml: str):\n"
        "    return {'items': [{'name': ET.fromstring(xml).find('n').attrib['t']}]}\n",
        encoding="utf-8",
    )

    class CoderMustNotRun:
        def implement(self, **kwargs):
            raise AssertionError("coder must be skipped for an already optimal script")

    event_hub = FakeEventHub()
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=event_hub,
        page_path="meituan",
        collection_schema='{"items": [{"name": ""}]}',
        coder_session=CoderMustNotRun(),
    )
    toolbox.collector_uiautomate()
    toolbox.save_baseline('{"items": [{"name": "cup"}]}')
    event_hub.xml = '<h><n t="bowl"/></h>'
    toolbox.collector_swipe_up(100, 1000)
    toolbox.save_baseline('{"items": [{"name": "bowl"}]}')

    result = json.loads(toolbox.consult_coder())

    assert result["ok"] is True
    assert result["skipped"] is True
    assert result["reason"] == "existing_script_already_optimal"
    assert result["evaluation"]["score"] == 100
    assert result["verification"]["ready"] is True
    assert [call[0] for call in event_hub.calls].count("uiautomate") == 2


def test_collector_rejects_coder_consultation_before_two_distinct_pairs(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text(
        "def run(xml: str):\n    return {'items': []}\n",
        encoding="utf-8",
    )

    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=FakeEventHub(),
        page_path="meituan",
        collection_schema='{"items": []}',
        coder_session=SimpleNamespace(implement=lambda **_: "must not run"),
    )
    toolbox.collector_uiautomate()
    toolbox.save_baseline('{"items": []}')

    result = json.loads(toolbox.consult_coder())

    assert result["ok"] is False
    assert result["error_type"] == "CollectorTrainingCasesInsufficient"
    assert result["recorded_cases"] == 1
    assert result["required_cases"] == 2


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
    assert result["evaluation"]["score"] == 100
    assert result["evaluation"]["errors"] == []
    assert result["evaluation"]["missing"] == []
    assert len(result["evaluation"]["cases"]) == 1
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
    assert replaced["evaluation"]["errors"] == ["cases[0].result.items[0].name"]


def test_sniff_run_keeps_evaluating_saved_pairs_after_page_moves(tmp_path: Path) -> None:
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

    assert result["ok"] is True
    assert result["evaluation"]["score"] == 100
    assert result["evaluated_cases"] == 1


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

    coder_toolbox = CollectorCoderToolbox(
        mechanism=toolbox.mechanism,
        page_path="meituan",
    )
    patched = json.loads(
        coder_toolbox.apply_run_agent_py_patch(
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
    assert second["evaluation"]["errors"] == ["cases[1].result.items[0].name"]
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
            self.tools["save_merchant_profile"].func('{"name": "商家A"}')
            self.tools["save_baseline"].func('{"items": [{"name": "cup"}]}')
            self.tools["sniff_run"].func()
            self.tools["collector_swipe_up"].func(100, 1000)
            self.tools["save_baseline"].func('{"items": [{"name": "bowl"}]}')
            self.tools["sniff_run"].func()
            self.tools["collector_tap"].func(500, 300)
            return {"messages": [{"role": "assistant", "content": "Ready. Score 100."}]}

    def create_agent(**kwargs):
        captured["kwargs"] = kwargs
        return FakeAgent(kwargs["tools"])

    page_dir = tmp_path / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
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
            page_path="meituan/美食/商家",
            page_description="merchant page",
            merchant_schema='{"name": ""}',
            collection_schema='{"items": [{"name": ""}]}',
            runtime=SimpleNamespace(
                state={
                    "page_context_path": "meituan/美食/商家",
                    "page_visit_id": "visit-merchant",
                }
            ),
        )
    )

    assert result["ok"] is True
    assert result["collector_message"] == "Ready. Score 100."
    assert result["trained"] is True
    assert result["verification"]["ready"] is True
    assert result["verification"]["validated_states"] == 2
    assert manager.training_registry.is_trained("visit-merchant", "meituan/美食/商家") is True
    assert result["restoration"]["restored"] is True
    assert captured["kwargs"]["name"] == "collector"
    assert captured["kwargs"]["system_prompt"]
    assert captured["config"]["recursion_limit"] == 1000
    assert "merchant page" in captured["payload"]["messages"][0]["content"]


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


def test_restore_to_top_requires_successful_reset_tap_without_device_checks(tmp_path: Path) -> None:
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

    assert restoration == {
        "restored": False,
        "strategy": "reset_tap",
        "tap_attempted": False,
        "tap_succeeded": False,
        "error": "reset_tap_not_executed",
    }
    assert event_hub.calls == []
    assert toolbox.training_result() == before


def test_collector_tap_is_locked_until_multi_state_verification_is_ready(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.agent.py").write_text(
        "from xml.etree import ElementTree as ET\n"
        "def run(xml: str):\n"
        "    name = ET.fromstring(xml).find('n').attrib['t']\n"
        "    return {'items': [{'name': name}]}\n",
        encoding="utf-8",
    )
    event_hub = FakeEventHub()
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=event_hub,
        page_path="meituan/美食/商家",
        merchant_schema='{"name": ""}',
        collection_schema='{"items": [{"name": ""}]}',
        requires_merchant_profile=True,
    )

    locked = json.loads(toolbox.collector_tap(500, 300))
    toolbox.collector_uiautomate()
    toolbox.save_merchant_profile('{"name": "商家A"}')
    toolbox.save_baseline('{"items": [{"name": "cup"}]}')
    toolbox.sniff_run()
    toolbox.collector_swipe_up(100, 1000)
    event_hub.xml = '<h><n t="bowl"/></h>'
    toolbox.collector_uiautomate()
    toolbox.save_baseline('{"items": [{"name": "bowl"}]}')
    toolbox.sniff_run()
    tapped = toolbox.collector_tap(500, 300)
    repeated = json.loads(toolbox.collector_tap(500, 300))
    restoration = toolbox.restore_to_top()

    assert locked["ok"] is False
    assert locked["error_type"] == "CollectorTapLocked"
    assert event_hub.calls[-1][0] == "tap"
    assert isinstance(tapped, str)
    assert repeated["error_type"] == "CollectorTapAlreadyUsed"
    assert restoration["restored"] is True
    assert restoration["strategy"] == "reset_tap"
    assert restoration["tap_succeeded"] is True
    assert "top_state_fingerprint" not in restoration


def test_collector_tap_stays_visible_and_rejects_calls_until_verification_ready(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.agent.py").write_text(
        "from xml.etree import ElementTree as ET\n"
        "def run(xml: str):\n"
        "    name = ET.fromstring(xml).find('n').attrib['t']\n"
        "    return {'items': [{'name': name}]}\n",
        encoding="utf-8",
    )
    event_hub = FakeEventHub()
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=event_hub,
        page_path="meituan/美食/商家",
        merchant_schema='{"name": ""}',
        collection_schema='{"items": [{"name": ""}]}',
        requires_merchant_profile=True,
    )
    tools = toolbox.create_tools()
    tool_names = [tool.name for tool in tools]
    assert "collector_tap" in tool_names
    locked = json.loads(toolbox.collector_tap(500, 300))
    assert locked["error_type"] == "CollectorTapLocked"

    toolbox.collector_uiautomate()
    toolbox.save_merchant_profile('{"name": "商家A"}')
    toolbox.save_baseline('{"items": [{"name": "cup"}]}')
    toolbox.sniff_run()
    toolbox.collector_swipe_up(100, 1000)
    event_hub.xml = '<h><n t="bowl"/></h>'
    toolbox.collector_uiautomate()
    toolbox.save_baseline('{"items": [{"name": "bowl"}]}')
    toolbox.sniff_run()
    assert toolbox.collector_tap(500, 300) == event_hub.xml
    already_used = json.loads(toolbox.collector_tap(500, 300))
    assert already_used["error_type"] == "CollectorTapAlreadyUsed"
    assert [tool.name for tool in toolbox.create_tools()] == tool_names


def test_collector_tap_rejects_non_merchant_page_calls_without_being_hidden(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text(
        "def run(xml: str):\n    return {'items': []}\n",
        encoding="utf-8",
    )
    event_hub = FakeEventHub()
    toolbox = CollectorToolbox(
        mechanism=PageMechanism(tmp_path),
        event_hub=event_hub,
        page_path="meituan",
    )

    assert "collector_tap" in [tool.name for tool in toolbox.create_tools()]
    result = json.loads(toolbox.collector_tap(500, 300))

    assert result["ok"] is False
    assert result["error_type"] == "CollectorTapNotApplicable"
    assert event_hub.calls == []


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

        def tap(self, x, y):
            self.calls.append(("tap", x, y))
            return make_error_result("command_error", "cannot restore")

    class FakeAgent:
        def __init__(self, tools) -> None:
            self.tools = {tool.name: tool for tool in tools}

        def invoke(self, payload, config=None):
            self.tools["collector_uiautomate"].func()
            self.tools["save_merchant_profile"].func('{"name": "商家A"}')
            self.tools["save_baseline"].func('{"items": [{"name": "cup"}]}')
            self.tools["sniff_run"].func()
            self.tools["collector_swipe_up"].func(100, 1000)
            self.tools["save_baseline"].func('{"items": [{"name": "bowl"}]}')
            self.tools["sniff_run"].func()
            self.tools["collector_tap"].func(500, 300)
            return {"messages": [{"role": "assistant", "content": "ready"}]}

    page_dir = tmp_path / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
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
            page_path="meituan/美食/商家",
            page_description="merchant",
            merchant_schema='{"name": ""}',
            collection_schema='{"items": [{"name": ""}]}',
            runtime=SimpleNamespace(
                state={
                    "page_context_path": "meituan/美食/商家",
                    "page_visit_id": "visit-restore-error",
                }
            ),
        )
    )

    assert result["verification"]["ready"] is True
    assert result["restoration"]["restored"] is False
    assert result["restoration"]["tap_attempted"] is True
    assert result["restoration"]["tap_succeeded"] is False
    assert result["trained"] is False
    assert manager.training_registry.is_trained(
        "visit-restore-error", "meituan/美食/商家"
    ) is False
