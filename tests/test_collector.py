from __future__ import annotations

from page_strategy_helpers import page_fields

from src.conductor.state import read_conductor_state, merge_fields
from src.agent_runtime.types.execution import EngineResult, EngineStatus

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.collector.toolbox import CollectorToolbox
from page_helpers import collector_history
from src.collector.history import CollectorHistory
from src.collector.core import CollectorTrainingCoordinator
from page_helpers import page_mechanism
from src.page.mechanism import PageMechanism
from src.device.services.page.bridge import PageBridge
from src.device.services.page.view import ReadOnlyPageView, MissingPageAlias
from src.page.collection_contract import CollectionContract

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6WAAAAABJRU5ErkJggg==")
GUIDE = '{"required_aliases":["1"]}\n\n# Product region\nFind the product text region by its role and position.'
SCRIPT = 'def run(view):\n    region = view.select_aaid(1)\n    return {"items": [{"name": e["value"]} for e in view.page_read(region["id"])["entries"]]}\n'


def xml(text):
    return f'<hierarchy><node class="android.widget.Button" text="{text}" clickable="true" bounds="[0,200][1000,1800]"/></hierarchy>'


class FakeHub:
    def __init__(self):
        self.text = "cup"
        self.calls = []
        self.page_bridge = PageBridge(lambda: xml(self.text))
        self.device = SimpleNamespace(pull_file=self.pull)

    def capture_png(self):
        self.calls.append("screenshot")
        return PNG

    def pull(self, remote, local):
        Path(local).write_bytes(PNG)
        return local

    def swipe_up(self, x, y):
        self.calls.append("swipe")
        self.text = "bowl"
        return self.page_bridge.refresh()

    swipe_up_long = swipe_up
    swipe_down = swipe_up
    swipe_to_random = swipe_up

    def tap(self, x, y):
        self.calls.append("tap")
        self.text = "cup"
        return self.page_bridge.refresh()


def setup(tmp_path, *, history=None, visit="a", run="probe-a"):
    page = tmp_path / "pages" / "meituan"
    page.mkdir(parents=True, exist_ok=True)
    for name, content in (("PAGE.md", "Merchant"), ("task.md", "Collect"), ("COLLECT.md", GUIDE), ("run.agent.py", SCRIPT)):
        if not (page / name).exists():
            (page / name).write_text(content)
    mechanism = page_mechanism(tmp_path / "pages")
    manual = page / "PAGE.md"
    manual.write_text(manual.read_text().replace("page_strategy: general", "page_strategy: merchant"))
    if mechanism.collection_contract("meituan") is None:
        mechanism.save_collection_contract("meituan", CollectionContract.from_dict({"primary_key": ["name"], "required_fields": ["name"]}))
    hub = FakeHub()
    toolbox = CollectorToolbox(mechanism=mechanism, event_hub=hub, page_path="meituan",
        page_visit_id=visit, run_id=run, history=history or collector_history(tmp_path / "history"),
        merchant_schema={"name": ""}, collection_schema={"items": [{"name": ""}]},
        requires_merchant_profile=True)
    toolbox._page_call("page_view", {})
    toolbox.read_collection_guide()
    toolbox.screenshot()
    toolbox.save_merchant_profile('{"name":"Merchant"}')
    return toolbox


def mark(toolbox):
    root = json.loads(toolbox._page_call("page_view", {}))
    key = root["regions"][0]["id"]
    toolbox._page_call("page_node", {"key": key})
    toolbox._page_call("update_aaid", {"id": key, "value": "1"})


def verify(toolbox, name):
    mark(toolbox)
    result = json.loads(toolbox.capture_validation_case())
    assert "run_results" not in result
    toolbox.screenshot()
    toolbox.save_baseline(json.dumps({"items": [{"name": name}]}))
    return json.loads(toolbox.sniff_run())


def train(toolbox):
    verify(toolbox, "cup")
    toolbox.return_to_live_page()
    toolbox.collector_swipe_to_random(500, 1200)
    result = verify(toolbox, "bowl")
    assert result["summary"]["ready"]
    return result


def test_visual_workflow_locks_state_and_hides_output(tmp_path):
    t = setup(tmp_path)
    mark(t)
    result = json.loads(t.capture_validation_case())
    assert result["awaiting"] == "screenshot_then_save_baseline"
    with pytest.raises(ValueError, match="screenshot"):
        t.save_baseline('{"items":[{"name":"cup"}]}')
    with pytest.raises(ValueError, match="frozen"):
        t.collector_swipe_up(1, 1000)
    before = t.event_hub.calls[:]
    t._page_call("page_view", {})
    assert t.event_hub.calls == before
    image = t.screenshot()
    assert image[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert json.loads(t.save_baseline('{"items":[{"name":"cup"}]}'))["saved"]
    report = json.loads(t.sniff_run())
    assert report["summary"]["failed_count"] == 0
    assert not report["summary"]["ready"]
    with pytest.raises(ValueError, match="overwritten"):
        t.save_baseline('{"items":[]}')


def test_baseline_repair_preserves_evidence_and_requires_regression(tmp_path):
    t = setup(tmp_path)
    verify(t, "wrong")
    case_id = t.validation.active_id
    screenshot = t.validation.cases[case_id]["image"]
    t.repair_baseline('{"items":[{"name":"cup"}]}', "The saved screenshot reads cup, not wrong.")
    assert not t.training_result()["ready"]
    assert t.validation.cases[case_id]["image"] == screenshot
    assert t.validation.cases[case_id]["baseline_revisions"][0]["baseline"]["items"][0]["name"] == "wrong"
    reloaded = setup(tmp_path, history=t.history, visit="b")
    with pytest.raises(ValueError):
        reloaded.repair_baseline('{"items":[]}', "incorrect")
    reloaded.load_validation_case(case_id)
    reloaded.screenshot()
    reloaded.repair_baseline('{"items":[{"name":"bowl"}]}', "Reviewing the same image again reveals a transcription error.")
    assert len(reloaded.validation.cases[case_id]["baseline_revisions"]) == 2
    assert len(reloaded.validation.cases) == 1


def test_failed_reset_tap_can_be_retried(tmp_path):
    t = setup(tmp_path)
    train(t)
    t.return_to_live_page()
    original = t.event_hub.tap
    t.event_hub.tap = lambda x, y: ("failed", None)
    t.collector_tap(1, 500)
    assert not t.restore_to_top()["restored"]
    t.event_hub.tap = original
    t.collector_tap(1, 500)
    assert t._restored


def test_repeated_baseline_repairs_require_report_and_review(tmp_path):
    t = setup(tmp_path)
    verify(t, "wrong")
    first = json.loads(t.repair_baseline('{"items":[{"name":"cu"}]}', "Visible prefix is cu"))
    assert not first["baseline_review_required"]
    t.screenshot()
    unchanged = json.loads(t.repair_baseline('{"items":[{"name":"cu"}]}', "Same evidence"))
    assert not unchanged["changed"]
    second = json.loads(t.repair_baseline('{"items":[{"name":"cup"}]}', "Full visible label is cup"))
    assert second["baseline_repair_count"] == 2
    assert second["baseline_review_required"]
    assert "talk" in second["warning"]
    assert json.loads(t.sniff_run())["summary"]["baseline_review_required"]


def test_partial_visual_baseline_allows_full_script_fields(tmp_path):
    t = setup(tmp_path)
    t.collection_schema = {"items": [{"name": "", "price": 0}]}
    t.mechanism.write_run_agent_py("meituan", 'def run(view):\n    return {"items": [{"name": "large cup", "price": 10}]}\n')
    report = verify(t, "cup")
    assert report["summary"]["failed_count"] == 0


def test_two_states_require_random_sample_and_restore(tmp_path):
    t = setup(tmp_path)
    verify(t, "cup")
    t.return_to_live_page()
    t.collector_swipe_up(1, 1000)
    verify(t, "bowl")
    assert not t.training_result()["ready"]
    t.return_to_live_page()
    t.collector_swipe_to_random(1, 1000)
    verify(t, "bowl")
    assert t.training_result()["ready"]
    assert not t.restore_to_top()["restored"]
    t.return_to_live_page()
    t.collector_tap(1, 500)
    assert t.restore_to_top()["restored"]
    assert "tap" in t.event_hub.calls


def test_sniff_run_reports_only_failed_cases(tmp_path):
    t = setup(tmp_path)
    verify(t, "wrong")
    failed_id = t.validation.active_id
    t.return_to_live_page()
    t.collector_swipe_to_random(500, 1200)
    report = verify(t, "bowl")
    assert set(report) == {"summary", "run_results"}
    assert report["summary"]["case_count"] == 2
    assert report["summary"]["passed_count"] == 1
    assert report["summary"]["failed_count"] == 1
    assert report["run_results"] == [{
        "case_id": failed_id,
        "ground_truth": {"items": [{"name": "wrong"}]},
        "run_result": {"items": [{"name": "cup"}]},
    }]
    assert len(t.validation.grades) == 2


def test_failed_case_cannot_be_evaded_or_rebased(tmp_path):
    t = setup(tmp_path)
    mark(t)
    t.capture_validation_case()
    t.screenshot()
    t.save_baseline('{"items":[{"name":"wrong"}]}')
    report = json.loads(t.sniff_run())
    assert report["summary"]["failed_count"] > 0
    for action in (lambda: t.abandon_validation("snapshot_mismatch"),
                   lambda: t.collector_tap(1, 500)):
        with pytest.raises(ValueError):
            action()
    t.return_to_live_page()
    assert json.loads(t.sniff_run())["summary"]["failed_count"] > 0


def test_patch_invalidates_training_and_preserves_scoped_file_tools(tmp_path):
    t = setup(tmp_path)
    train(t)
    first = json.loads(t.read_run_agent_py())
    assert first["content"]
    assert json.loads(t.read_run_agent_py())["content_omitted"]
    patch = "*** Begin Patch\n*** Update File: run.agent.py\n@@\n def run(view):\n+    # Stable extraction contract.\n*** End Patch\n"
    assert json.loads(t.apply_run_agent_py_patch(patch))["ok"]
    assert not t.training_result()["ready"]
    assert json.loads(t.sniff_run())["summary"]["ready"]
    forbidden = patch.replace("run.agent.py", "../run.agent.py")
    assert not json.loads(t.apply_run_agent_py_patch(forbidden))["ok"]


def test_history_persists_all_visual_cases_and_reloads(tmp_path):
    t = setup(tmp_path)
    train(t)
    t.return_to_live_page()
    t.collector_tap(1, 500)
    t.persist_history()
    files = list((tmp_path / "history").rglob("record.json"))
    assert len(files) == 2
    stored = json.loads(files[0].read_text())
    assert stored["image"].startswith("data:image/png;base64,")
    assert stored["baseline"] is not None
    assert stored["snapshot"]["aliases"] == {"1": [0]}
    second = setup(tmp_path, visit="b")
    assert len(second.validation.historical_ids) == 2
    key = next(iter(second.validation.historical_ids))
    before = list(second.event_hub.calls)
    second.load_validation_case(key)
    second.screenshot()
    assert second.event_hub.calls == before
    with pytest.raises(ValueError):
        second.collector_swipe_up(1, 1000)
    with pytest.raises(ValueError):
        second.save_baseline('{"items":[]}')


def test_guide_change_retires_all_old_cases_and_requires_fresh_sampling(tmp_path):
    t = setup(tmp_path)
    train(t)
    t.return_to_live_page()
    t.collector_tap(1, 500)
    t.persist_history()
    second = setup(tmp_path, visit="b")
    key = next(iter(second.validation.historical_ids))
    second.load_validation_case(key)
    second._page_call("page_read", {})
    second.screenshot()
    second.return_to_live_page()
    verify(second, "cup")
    old_ids = set(second.validation.cases)
    result = json.loads(second.write_collection_guide(GUIDE + "\nUse control state to distinguish nearby regions."))
    assert result["invalidated_cases"] == 3
    assert not second.validation.cases
    assert not second.validation.historical_ids
    assert second.validation.active_id is None
    assert second.validation.frozen is None
    assert not second.training_result()["ready"]
    assert not second.history.load("meituan", run_id=second.run_id)
    archived = list((tmp_path / "history/runs/probe-a/meituan/invalidated").rglob("record.json"))
    assert len(archived) == 3
    assert {json.loads(file.read_text())["case_id"] for file in archived} == old_ids
    with pytest.raises(ValueError, match="Prepare visual cases"):
        second.sniff_run()
    train(second)
    assert not old_ids.intersection(second.validation.cases)
    assert set(setup(tmp_path, visit="c").validation.cases) == set(second.validation.cases)


def test_unchanged_guide_preserves_cases_and_readiness(tmp_path):
    t = setup(tmp_path)
    train(t)
    cases = set(t.validation.cases)
    stamp = t.validation.tested_stamp
    result = json.loads(t.write_collection_guide(GUIDE))
    assert result["updated"] is False
    assert set(t.validation.cases) == cases
    assert t.validation.tested_stamp == stamp
    assert t.training_result()["ready"]


def test_invalid_guide_does_not_retire_cases(tmp_path):
    t = setup(tmp_path)
    train(t)
    cases = set(t.validation.cases)
    with pytest.raises(ValueError):
        t.write_collection_guide('{"required_aliases":["x1"]}\nInvalid')
    assert set(t.validation.cases) == cases
    assert len(t.history.load("meituan", run_id=t.run_id)) == 2


def test_script_regression_against_history_blocks_readiness(tmp_path):
    t = setup(tmp_path)
    train(t)
    t.return_to_live_page()
    t.collector_tap(1, 500)
    t.persist_history()
    second = setup(tmp_path, visit="b")
    key = next(iter(second.validation.historical_ids))
    expected = second.validation.cases[key]["baseline"]["items"][0]["name"]
    second.mechanism.write_run_agent_py("meituan", 'def run(view):\n    return {"items": []}\n')
    second.load_validation_case(key)
    report = json.loads(second.sniff_run())
    assert key in {case["case_id"] for case in report["run_results"]}
    assert expected
    assert not second.training_result()["ready"]


def test_guide_creation_requires_both_view_and_image(tmp_path):
    t = setup(tmp_path)
    (tmp_path / "pages/meituan/COLLECT.md").unlink()
    t._saw_screenshot = False
    assert json.loads(t.read_collection_guide())["error_type"] == "guide_missing"
    with pytest.raises(ValueError):
        t.write_collection_guide(GUIDE)
    t._page_call("page_read", {})
    t.screenshot()
    assert json.loads(t.write_collection_guide(GUIDE))["ok"]
    with pytest.raises(ValueError):
        t.write_collection_guide('{"required_aliases":["x1"]}\nInvalid alias')
    with pytest.raises(ValueError):
        t.mechanism.write_collection_guide("../escape", GUIDE)


def test_view_has_no_mutating_tools_and_restores_paths(tmp_path):
    t = setup(tmp_path)
    mark(t)
    snapshot = t.event_hub.page_bridge.snapshot()
    view = ReadOnlyPageView(snapshot)
    assert view.select_aaid(1)["id"] >= 0
    assert not hasattr(view, "update_aaid")
    assert not hasattr(view, "xml")
    assert view.page_read()["entries"][0]["value"] == "cup"
    snapshot["aliases"] = {}
    assert view.select_aaid(1)
    with pytest.raises(MissingPageAlias):
        ReadOnlyPageView(snapshot).select_aaid(1)


def test_tools_are_fixed_without_coder_and_report_errors(tmp_path):
    t = setup(tmp_path)
    tools = {tool.name: tool for tool in t.create_tools()}
    assert {"read_run_agent_py", "apply_run_agent_py_patch", "screenshot", "load_validation_case"} <= tools.keys()
    assert "consult_coder" not in tools
    assert "uiautomate" not in tools
    before = list(tools)
    mark(t)
    t.capture_validation_case()
    result = json.loads(tools["collector_swipe_up"].invoke({"x": 1, "y": 1000}))
    assert result["ok"] is False
    assert before == [tool.name for tool in t.create_tools()]


def test_abandon_only_unscored_samples(tmp_path):
    t = setup(tmp_path)
    mark(t)
    t.capture_validation_case()
    t.screenshot()
    assert json.loads(t.abandon_validation("snapshot_mismatch"))["abandoned"]
    assert not t.validation.cases


def test_prepare_collector_uses_verified_results_not_final_text(tmp_path, monkeypatch):
    t = setup(tmp_path)
    from src.agent_runtime.contracts import AgentHandle
    from src.agent_hypervisor.host import Host
    from src.channel.messages import InBoundMessage
    host = Host()
    root = host.router.register_root()
    host.router.deliver_user(InBoundMessage("test", "chat", "1", "start", "user"))
    parent = host.router.register_child(root)
    started = []
    def start(engine):
        started.append(engine)
        host._workers[engine.handle.agent_id] = engine
    monkeypatch.setattr(host, "start", start)
    monkeypatch.setattr("src.collector.core.CollectorEngine", lambda **kwargs: SimpleNamespace(handle=kwargs["handle"]))
    manager = CollectorTrainingCoordinator(handle=host.handle(parent), model="fake", mechanism=t.mechanism, event_hub=t.event_hub)
    manager.history = collector_history(tmp_path / "history")
    result = json.loads(manager.prepare_collector("meituan", "page", '{"name":""}',
        '{"items":[{"name":""}]}', SimpleNamespace(context=None, state=read_conductor_state({**page_fields("meituan", "visit", "merchant"), "probe": {"run_id": "probe-a"}}))))
    assert result["status"] == "submitted"
    child = started[0].handle.agent_id
    assert result["subagent_id"] == child
    assert host.router.read(child, wait=False)[0].task_id == result["task_id"]
    assert not manager.training_registry.is_trained("visit")


def test_ready_does_not_unlock_device_while_view_is_frozen(tmp_path):
    t = setup(tmp_path)
    train(t)
    with pytest.raises(ValueError):
        t.collector_tap(1, 500)


def test_batch_sampling_never_runs_script_and_regression_never_moves_device(tmp_path, monkeypatch):
    t = setup(tmp_path)
    calls = []
    original = t.mechanism.run_page_script

    def run(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(t.mechanism, "run_page_script", run)
    for name in ("cup", "bowl"):
        mark(t)
        t.capture_validation_case()
        t.screenshot()
        saved = json.loads(t.save_baseline(json.dumps({"items": [{"name": name}]})))
        assert saved["saved"] and "eval_grades" not in saved
        t.return_to_live_page()
        if name == "cup":
            t.collector_swipe_to_random(500, 1200)
    assert not calls
    assert len(t.history.load("meituan", run_id=t.run_id)) == 2
    before = t.event_hub.calls[:]
    ids = set(t.validation.cases)
    report = json.loads(t.sniff_run())
    assert report["summary"]["ready"]
    assert len(calls) == 2
    assert t.event_hub.calls == before
    assert t.validation.active_id is None and set(t.validation.cases) == ids


def test_failing_evidence_survives_restart_before_success(tmp_path):
    t = setup(tmp_path)
    mark(t)
    t.capture_validation_case()
    t.screenshot()
    t.save_baseline('{"items":[{"name":"wrong"}]}')
    second = setup(tmp_path, visit="b")
    assert len(second.validation.historical_ids) == 1
    assert json.loads(second.sniff_run())["summary"]["failed_count"] > 0


def test_new_run_excludes_old_failed_cases_but_keeps_archives(tmp_path):
    old = setup(tmp_path, run="old-run")
    verify(old, "obsolete-baseline")
    archived = {p: p.read_bytes() for p in (tmp_path / "history").rglob("record.json")}
    assert archived
    current = setup(tmp_path, run="new-run")
    assert not current.validation.cases
    assert json.loads(current.list_validation_cases()) == []
    with pytest.raises(ValueError, match="Prepare visual cases"):
        current.sniff_run()
    for case_id in old.validation.cases:
        with pytest.raises(ValueError, match="Unknown case"):
            current.load_validation_case(case_id)
    result = train(current)
    assert result["summary"]["ready"]
    assert result["summary"]["case_count"] == len(current.validation.cases)
    assert not current.validation.cases.keys() & old.validation.cases.keys()
    assert result["run_results"] == []
    assert {p: p.read_bytes() for p in archived} == archived


def test_same_run_recreated_collector_keeps_all_prepared_cases(tmp_path):
    first = setup(tmp_path, run="shared-run", visit="first-visit")
    verify(first, "cup")
    second = setup(tmp_path, run="shared-run", visit="second-visit")
    verify(second, "cup")
    # Recreate the toolbox and storage object, as after retrying prepare_collector.
    resumed = setup(tmp_path, run="shared-run", visit="second-visit")
    report = json.loads(resumed.sniff_run())
    assert report["summary"]["case_count"] == 2
    assert resumed.validation.cases.keys() == second.validation.cases.keys()
    assert report["run_results"] == []
    assert report["summary"]["failed_count"] == 0


def test_prepare_collector_requires_runtime_run_identity(tmp_path, monkeypatch):
    box = setup(tmp_path)
    from gateway_helpers import GatewayHandle, RecordingChannel
    manager = CollectorTrainingCoordinator(handle=GatewayHandle(RecordingChannel()).handle, model="fake", mechanism=box.mechanism, event_hub=box.event_hub)
    monkeypatch.setattr("src.collector.core.CollectorEngine", lambda **_: pytest.fail("must not launch collector"))
    with pytest.raises(ValueError, match="probe_run_id"):
        manager.prepare_collector("meituan", "page", '{"name":""}', '{"items":[{"name":""}]}',
            SimpleNamespace(state=read_conductor_state(page_fields("meituan", "visit", "merchant"))))


def test_contract_reuse_is_noop_and_repair_requires_evidence(tmp_path):
    t = setup(tmp_path)
    path = tmp_path / "pages/meituan/PAGE.md"
    original = path.read_bytes(), path.stat().st_mtime_ns
    assert json.loads(t.save_collection_contract(["name"], []))["updated"] is False
    assert (path.read_bytes(), path.stat().st_mtime_ns) == original
    with pytest.raises(ValueError, match="schema"):
        t.save_collection_contract(["unknown"], [])


def test_missing_contract_is_defined_after_multiple_samples(tmp_path):
    t = setup(tmp_path)
    (tmp_path / "pages/meituan/PAGE.md").write_text("Merchant")
    with pytest.raises(ValueError, match="multiple"):
        t.save_collection_contract(["name"], [])
    for name in ("cup", "bowl"):
        mark(t)
        t.capture_validation_case()
        t.screenshot()
        t.save_baseline(json.dumps({"items": [{"name": name}]}))
        t.return_to_live_page()
        if name == "cup":
            t.collector_swipe_to_random(500, 1200)
    assert json.loads(t.save_collection_contract(["name"], []))["updated"]
    assert json.loads(t.sniff_run())["summary"]["ready"]
    assert "tap" not in t.event_hub.calls


def test_screenshot_tool_returns_image_blocks_to_langchain(tmp_path):
    from langchain_core.messages import ToolMessage
    t = setup(tmp_path)
    tool = next(tool for tool in t.create_tools() if tool.name == "screenshot")
    message = tool.invoke({"type": "tool_call", "id": "screen", "name": "screenshot", "args": {}})
    assert isinstance(message, ToolMessage)
    assert isinstance(message.content, list)
    assert message.content[1]["type"] == "image_url"


def test_frozen_view_pagination_and_copied_query_results(tmp_path):
    t = setup(tmp_path)
    mark(t)
    view = ReadOnlyPageView(t.event_hub.page_bridge.snapshot())
    first = view.page_read(offset=0, limit=1)
    assert len(first["entries"]) == 1
    first["entries"][0]["value"] = "changed"
    assert view.page_read()["entries"][0]["value"] == "cup"
    assert view.page_read(offset=1, limit=1)["entries"] == []


def test_legacy_xml_script_is_rejected(tmp_path):
    t = setup(tmp_path)
    mark(t)
    t.mechanism.write_run_agent_py("meituan", 'def run(xml):\n    return {"items":[]}\n')
    output = t.mechanism.run_page_script("meituan", ReadOnlyPageView(t.event_hub.page_bridge.snapshot()))
    assert not output["ok"]
    assert output["error_type"] == "TypeError"


def test_normal_swipe_threshold_uses_verified_live_short_and_long_samples(tmp_path):
    t = setup(tmp_path)
    verify(t, "cup")
    t.return_to_live_page()
    long = json.loads(t.collector_swipe_up_long(500, 1200))
    verify(t, "bowl")
    t.return_to_live_page()
    short = json.loads(t.collector_swipe_up(500, 1200))
    with pytest.raises(ValueError, match="Passing live states"):
        t.confirm_normal_swipes([long["operation_id"], short["operation_id"]])
    verify(t, "bowl")
    confirmed = json.loads(t.confirm_normal_swipes([long["operation_id"], short["operation_id"]]))
    assert confirmed["sample_count"] == 2
    assert confirmed["actions"] == ["swipe_up", "swipe_up_long"]
    assert confirmed["change_threshold"] == min(1, max(long["view_update_ratio"], short["view_update_ratio"]) + .05)
    assert t.training_result()["normal_swipe_profile"]["change_threshold"] == confirmed["change_threshold"]
    with pytest.raises(ValueError, match="both"):
        t.confirm_normal_swipes([short["operation_id"]])
    with pytest.raises(ValueError, match="live"):
        t.confirm_normal_swipes(["other-visit"])
    t.mechanism.write_run_agent_py(t.page_path, SCRIPT + "\n# changed\n")
    assert t.training_result()["normal_swipe_profile"] is None
    t.sniff_run()
    assert t.training_result()["normal_swipe_profile"] is not None


def test_random_swipes_do_not_supply_normal_threshold_evidence(tmp_path):
    t = setup(tmp_path)
    train(t)
    assert t.training_result()["normal_swipe_profile"] is None
    assert not t._swipe_samples


def test_move_records_view_ratio_even_when_node_ratio_saturates(tmp_path, monkeypatch):
    from test_page_change_hint import page, button, container, items
    t = setup(tmp_path)
    before = page(button("header"), container(items(2)), button("footer"))
    after = page(button("header"), container(items(20, "new")), button("footer"))
    t.event_hub.page_bridge.observe(before)
    monkeypatch.setattr(t.event_hub, "swipe_up", lambda x, y: t.event_hub.page_bridge.observe(after))
    result = json.loads(t.collector_swipe_up(500, 1200))
    sample = t._swipe_samples[result["operation_id"]]
    assert result["view_update_ratio"] == sample.ratio == 0.0
    assert result["node_update_ratio"] == 1.0


def test_manager_returns_and_registers_verified_swipe_threshold(tmp_path, monkeypatch):
    initial = setup(tmp_path)
    from src.collector.engine import CollectorEngine
    from src.collector.training import CollectorTrainingRegistry
    from src.agent_runtime.contracts import TaskResult
    engine = object.__new__(CollectorEngine)
    engine._toolbox = initial
    engine._registry = CollectorTrainingRegistry()
    box = initial
    box._page_call("page_view", {})
    box.read_collection_guide()
    box.screenshot()
    box.save_merchant_profile('{"name":"Merchant"}')
    train(box)
    box.return_to_live_page()
    long = json.loads(box.collector_swipe_up_long(500, 1200))
    verify(box, "bowl")
    box.return_to_live_page()
    short = json.loads(box.collector_swipe_up(500, 1200))
    verify(box, "bowl")
    box.confirm_normal_swipes([long["operation_id"], short["operation_id"]])
    box.return_to_live_page()
    box.collector_tap(500, 500)
    mark(box)
    result = engine._prepare_report(TaskResult(status="completed", summary="Ready")).data
    assert result["trained"], result
    profile = result["normal_swipe_profile"]
    record = engine._registry.record_for(box.page_visit_id)
    assert record.change_threshold == profile["change_threshold"]
    assert record.normal_swipe_samples == profile["sample_count"] == 2


def test_move_records_nonzero_view_change_instead_of_node_fraction(tmp_path, monkeypatch):
    from test_page_change_hint import visible, button
    t = setup(tmp_path)
    before = visible(button('a', bounds='[0,0][100,100]'))
    after = visible(button('a', bounds='[0,0][100,20]'))
    t.event_hub.page_bridge.observe(before)
    monkeypatch.setattr(t.event_hub, 'swipe_up', lambda x, y: t.event_hub.page_bridge.observe(after))
    result = json.loads(t.collector_swipe_up(500, 1200))
    sample = t._swipe_samples[result['operation_id']]
    assert sample.ratio == result['view_update_ratio'] == .8
    assert result['node_update_ratio'] == pytest.approx(1 / 3)
