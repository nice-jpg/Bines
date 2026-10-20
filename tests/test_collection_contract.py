
from page_strategy_helpers import new_visit

from src.conductor.state import read_conductor_state, merge_fields
import json
from types import SimpleNamespace

import pytest

from src.page.collection_contract import CollectionContract
from src.common.utils.json import is_empty_json
from page_helpers import page_mechanism
from src.page.mechanism import PageMechanism
from workbook_helpers import collection_sink, workbook_service
from src.tools.page_notes.tools import create_page_notes_tool
from src.tools.collection import audit as collection_audit


def test_notes_and_contract_have_independent_sections(tmp_path):
    page = tmp_path / "app"
    page.mkdir()
    (page / "PAGE.md").write_text("# Original\nSchema stays here")
    (page / "task.md").write_text("Unchanged task")
    mechanism = page_mechanism(tmp_path)
    contract = CollectionContract.from_dict({"primary_key": ["name", "size"], "required_fields": ["price"]})
    mechanism.save_collection_contract("app", contract)
    tool = create_page_notes_tool(mechanism)
    tool.func("Use screenshots for ambiguous controls", SimpleNamespace(state=read_conductor_state({"page_context_path": "app", "page_visit": new_visit("merchant" if "app".endswith("/商家") else "general"), "page_visit_id": "test-visit"})))
    assert mechanism.collection_contract("app") == contract
    assert "Schema stays here" in (page / "PAGE.md").read_text()
    assert (page / "task.md").read_text() == "Unchanged task"
    with pytest.raises(ValueError):
        mechanism.update_page_notes("app", "<!-- collection-contract:end -->")
    with pytest.raises(ValueError):
        mechanism.update_page_notes("../app", "escape")


def test_empty_and_joint_identity():
    assert all(is_empty_json(v) for v in [None, " ", [], {}])
    assert not is_empty_json(0) and not is_empty_json(False)
    contract = CollectionContract.from_dict({"primary_key": ["name", "size"], "required_fields": ["price"]})
    assert contract.key({"name": "cup", "size": "S"}) != contract.key({"name": "cup", "size": "L"})
    assert contract.missing({"name": "cup", "size": "S", "price": 0}) == []


def test_primary_keys_survive_reload_and_save_failure(tmp_path, monkeypatch):
    import src.tools.workbooks.excel as module
    contract = CollectionContract.from_dict({"primary_key": ["name"], "required_fields": ["price"]})
    provider = workbook_service(tmp_path)
    sink = collection_sink(provider)
    def persist(sink, price):
        return sink.persist_run_result(run_result={"ok": True, "result": {
            "merchant": {"name": "shop"}, "items": [{"name": "cup", "price": price}]}},
            append_items=True, contract=contract)
    with monkeypatch.context() as patch:
        patch.setattr(module, "_save_workbook", lambda *a: (_ for _ in ()).throw(OSError("disk")))
        with pytest.raises(OSError):
            persist(sink, 10)
    assert persist(sink, 10)["appended_items"] == 1
    # Rebuild the in-memory identity cache from the persisted workbook.
    sink._merchant_sheets.clear()
    assert persist(sink, 20)["appended_items"] == 0


def test_audit_sampling_is_offline_and_fail_open(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(collection_audit, "capture_png", lambda hub: calls.append(1) or b"png")
    args = (object(), tmp_path, "app/merchant", "visit", {"snapshot_id": "one"},
            {"ok": False}, {}, None, {"tool": "swipe_up"}, "version")
    monkeypatch.setattr(collection_audit.random, "random", lambda: .9)
    collection_audit.sample_collection(*args)
    assert not calls
    monkeypatch.setattr(collection_audit.random, "random", lambda: .01)
    collection_audit.sample_collection(*args)
    files = list(tmp_path.rglob("record.json"))
    assert len(files) == 1
    assert json.loads(files[0].read_text())["snapshot"]["snapshot_id"] == "one"
    monkeypatch.setattr(collection_audit, "capture_png", lambda hub: (_ for _ in ()).throw(OSError("offline")))
    collection_audit.sample_collection(*args)
    assert len(list(tmp_path.rglob("record.json"))) == 1


def test_screenshot_tool_delivers_image_not_path(monkeypatch):
    from src.tools.device import screenshots as screenshot
    hub = SimpleNamespace(screenshot=lambda: "/device/image.png")
    monkeypatch.setattr(screenshot, "capture_png", lambda _: b"png")
    from src.tools.device.operations import create_event_hub_tools
    tool = create_event_hub_tools(hub, tool_names=("screenshot",))[0]
    result = tool.invoke({})
    assert result[0]["image_url"]["url"].startswith("data:image/png;base64,")
