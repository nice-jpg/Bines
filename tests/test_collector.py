from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.collector import CollectorToolbox, CollectorHistory, CollectorManager
from src.page_mechanism import PageMechanism, PagePolicy
from src.tools.page_bridge import PageBridge
from src.page_view import ReadOnlyPageView, MissingPageAlias
from src.collection_contract import CollectionContract

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

    def screenshot(self):
        self.calls.append("screenshot")
        return "/sdcard/screen.png"

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
    mechanism = PageMechanism(tmp_path / "pages", page_policies={"meituan": PagePolicy(role="merchant", requires_collector=True, max_product_entry_taps=1)})
    if mechanism.collection_contract("meituan") is None:
        mechanism.save_collection_contract("meituan", CollectionContract.from_dict({"primary_key": ["name"], "required_fields": ["name"]}))
    hub = FakeHub()
    toolbox = CollectorToolbox(mechanism=mechanism, event_hub=hub, page_path="meituan",
        page_visit_id=visit, run_id=run, history=history or CollectorHistory(tmp_path / "history"),
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
    assert result["eval_grades"]["ready"]
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
    assert report["eval_grades"]["score"] == 100
    assert not report["eval_grades"]["ready"]
    with pytest.raises(ValueError, match="overwritten"):
        t.save_baseline('{"items":[]}')


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


def test_failed_case_cannot_be_evaded_or_rebased(tmp_path):
    t = setup(tmp_path)
    mark(t)
    t.capture_validation_case()
    t.screenshot()
    t.save_baseline('{"items":[{"name":"wrong"}]}')
    report = json.loads(t.sniff_run())
    assert report["eval_grades"]["score"] < 100
    for action in (lambda: t.abandon_validation("snapshot_mismatch"),
                   lambda: t.collector_tap(1, 500)):
        with pytest.raises(ValueError):
            action()
    t.return_to_live_page()
    assert json.loads(t.sniff_run())["eval_grades"]["score"] < 100


def test_patch_invalidates_training_and_preserves_scoped_file_tools(tmp_path):
    t = setup(tmp_path)
    train(t)
    first = json.loads(t.read_run_agent_py())
    assert first["content"]
    assert json.loads(t.read_run_agent_py())["content_omitted"]
    patch = "*** Begin Patch\n*** Update File: run.agent.py\n@@\n def run(view):\n+    # Stable extraction contract.\n*** End Patch\n"
    assert json.loads(t.apply_run_agent_py_patch(patch))["ok"]
    assert not t.training_result()["ready"]
    assert json.loads(t.sniff_run())["eval_grades"]["ready"]
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
    assert len(second.historical_ids) == 2
    key = next(iter(second.historical_ids))
    before = list(second.event_hub.calls)
    second.load_validation_case(key)
    second.screenshot()
    assert second.event_hub.calls == before
    with pytest.raises(ValueError):
        second.collector_swipe_up(1, 1000)
    with pytest.raises(ValueError):
        second.save_baseline('{"items":[]}')


def test_guide_change_requires_reannotation_of_history(tmp_path):
    t = setup(tmp_path)
    train(t)
    t.return_to_live_page()
    t.collector_tap(1, 500)
    t.persist_history()
    second = setup(tmp_path, visit="b")
    key = next(iter(second.historical_ids))
    second.load_validation_case(key)
    original = second.cases[key]["baseline"]
    second._page_call("page_read", {})
    second.screenshot()
    second.write_collection_guide(GUIDE + "\nUse control state to distinguish nearby regions.")
    result = json.loads(second.sniff_run())
    assert result["eval_grades"]["cases"][key]["errors"] == ["remark_required"]
    mark(second)
    result = json.loads(second.sniff_run())
    assert result["eval_grades"]["cases"][key]["score"] == 100
    assert second.cases[key]["baseline"] == original


def test_script_regression_against_history_blocks_readiness(tmp_path):
    t = setup(tmp_path)
    train(t)
    t.return_to_live_page()
    t.collector_tap(1, 500)
    t.persist_history()
    second = setup(tmp_path, visit="b")
    key = next(iter(second.historical_ids))
    expected = second.cases[key]["baseline"]["items"][0]["name"]
    second.mechanism.write_run_agent_py("meituan", 'def run(view):\n    return {"items": []}\n')
    second.load_validation_case(key)
    report = json.loads(second.sniff_run())
    assert report["eval_grades"]["cases"][key]["score"] < 100
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
    assert not t.cases


def test_prepare_collector_uses_verified_results_not_final_text(tmp_path, monkeypatch):
    t = setup(tmp_path)
    class FakeAgent:
        def __init__(self, tools):
            self.toolbox = next(tool for tool in tools if tool.name == "sniff_run").func.__wrapped__.__self__
        def invoke(self, payload, config):
            box = self.toolbox
            box._page_call("page_view", {})
            box.read_collection_guide()
            box.screenshot()
            box.save_merchant_profile('{"name":"Merchant"}')
            train(box)
            box.return_to_live_page()
            box.collector_tap(1, 500)
            return {"messages": [{"content": "Ready."}]}
    monkeypatch.setattr("src.collector.core.create_runtime", lambda **kw: FakeAgent(kw["tools"]))
    manager = CollectorManager(model="fake", mechanism=t.mechanism, event_hub=t.event_hub)
    manager.history = CollectorHistory(tmp_path / "history")
    result = json.loads(manager.prepare_collector("meituan", "page", '{"name":""}',
        '{"items":[{"name":""}]}', SimpleNamespace(state={"page_context_path":"meituan","page_visit_id":"visit","probe_run_id":"probe-a"})))
    assert result["verification"]["ready"]
    assert result["collector_message"] == "Ready."
    assert result["trained"]
    assert len(manager.history.load("meituan", run_id="probe-a")) == 2


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
    ids = set(t.cases)
    report = json.loads(t.sniff_run())
    assert report["eval_grades"]["ready"]
    assert len(calls) == 2
    assert t.event_hub.calls == before
    assert t.active_id is None and set(t.cases) == ids


def test_failing_evidence_survives_restart_before_success(tmp_path):
    t = setup(tmp_path)
    mark(t)
    t.capture_validation_case()
    t.screenshot()
    t.save_baseline('{"items":[{"name":"wrong"}]}')
    second = setup(tmp_path, visit="b")
    assert len(second.historical_ids) == 1
    assert json.loads(second.sniff_run())["eval_grades"]["score"] < 100


def test_new_run_excludes_old_failed_cases_but_keeps_archives(tmp_path):
    old = setup(tmp_path, run="old-run")
    verify(old, "obsolete-baseline")
    archived = {p: p.read_bytes() for p in (tmp_path / "history").rglob("record.json")}
    assert archived
    current = setup(tmp_path, run="new-run")
    assert not current.cases
    assert json.loads(current.list_validation_cases()) == []
    with pytest.raises(ValueError, match="Prepare visual cases"):
        current.sniff_run()
    for case_id in old.cases:
        with pytest.raises(ValueError, match="Unknown case"):
            current.load_validation_case(case_id)
    result = train(current)
    assert result["eval_grades"]["ready"]
    assert set(result["ground_truths"]) == set(current.cases)
    assert not set(result["ground_truths"]) & old.cases.keys()
    assert {p: p.read_bytes() for p in archived} == archived


def test_same_run_recreated_collector_keeps_all_prepared_cases(tmp_path):
    first = setup(tmp_path, run="shared-run", visit="first-visit")
    verify(first, "cup")
    second = setup(tmp_path, run="shared-run", visit="second-visit")
    verify(second, "cup")
    # Recreate the toolbox and storage object, as after retrying prepare_collector.
    resumed = setup(tmp_path, run="shared-run", visit="second-visit")
    report = json.loads(resumed.sniff_run())
    assert len(report["cases"]) == 2
    assert set(report["ground_truths"]) == set(second.cases)
    assert report["eval_grades"]["score"] == 100


def test_prepare_collector_requires_runtime_run_identity(tmp_path, monkeypatch):
    box = setup(tmp_path)
    manager = CollectorManager(model="fake", mechanism=box.mechanism, event_hub=box.event_hub)
    monkeypatch.setattr(manager, "_build_agent", lambda _: pytest.fail("must not launch collector"))
    with pytest.raises(ValueError, match="probe_run_id"):
        manager.prepare_collector("meituan", "page", '{"name":""}', '{"items":[{"name":""}]}',
            SimpleNamespace(state={"page_context_path": "meituan", "page_visit_id": "visit"}))


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
    assert json.loads(t.sniff_run())["eval_grades"]["ready"]
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
