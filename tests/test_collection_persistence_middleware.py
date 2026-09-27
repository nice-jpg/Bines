from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from middleware.collection_persistence import CollectionPersistenceMiddleware
from middleware.page_context import PAGE_CONTEXT_PATH, PAGE_VISIT_ID
from middleware.probe_state import (
    COLLECTION_CONSECUTIVE_EMPTY_SWIPES,
    COLLECTION_LATEST_NEW_COUNT,
    COLLECTION_VISIT_SUMMARIES,
    MERCHANT_COLLECTION_DONE,
)
from page_mechanism import CollectorTrainingRegistry, PageMechanism
from tools.content_provider import CollectionWorkbookSink, ContentProvider, _read_xlsx
from src.tools.page_bridge import PageBridge


def _request(
    tool_name: str,
    *,
    overlay: bool = False,
    page_path: str = "meituan/美食/商家",
    page_visit_id: str = "visit-a",
) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool_name, "id": f"{tool_name}-1", "args": {}},
        tool=None,
        state={
            PAGE_CONTEXT_PATH: page_path,
            PAGE_VISIT_ID: page_visit_id,
        },
    )


def _tool_message(tool_name: str, content: str = "cup") -> ToolMessage:
    if tool_name == "prepare_collector":
        return ToolMessage(content=content, tool_call_id=f"{tool_name}-1", name=tool_name)
    bridge = PageBridge(lambda: f'<hierarchy><node text="{content}" class="android.widget.Button" bounds="[0,200][1000,1800]"/></hierarchy>')
    view, _ = bridge.refresh()
    bridge.query("update_aaid", {"id": view["regions"][0]["id"], "value": "1"})
    return ToolMessage(content=json.dumps({"summary": content}), artifact={"page_snapshot": bridge.snapshot()}, tool_call_id=f"{tool_name}-1", name=tool_name)


def _message(result: ToolMessage | Command) -> ToolMessage:
    if isinstance(result, ToolMessage):
        return result
    assert isinstance(result.update, dict)
    return result.update["messages"][-1]


def _apply_state(state: dict, result: ToolMessage | Command) -> None:
    if not isinstance(result, Command) or not isinstance(result.update, dict):
        return
    state.update({key: value for key, value in result.update.items() if key != "messages"})


def _payload(result: ToolMessage | Command) -> dict:
    return json.loads(_message(result).content)


def _middleware(tmp_path: Path) -> tuple[CollectionPersistenceMiddleware, CollectorTrainingRegistry, Path]:
    page_dir = tmp_path / "pages" / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.agent.py").write_text(
        "def run(view):\n"
        "    return {\n"
        "        'items': [{'name': view.page_read(view.select_aaid(1)['id'])['entries'][0]['value'], 'price': '10'}],\n"
        "    }\n",
        encoding="utf-8",
    )
    (page_dir / "COLLECT.md").write_text('{"required_aliases":["1"]}\nProduct region.')
    mechanism = PageMechanism(tmp_path / "pages")
    registry = CollectorTrainingRegistry()
    sink = CollectionWorkbookSink(ContentProvider(tmp_path / "workspace"))
    middleware = CollectionPersistenceMiddleware(
        mechanism=mechanism,
        training_registry=registry,
        workbook_sink=sink,
    )
    return middleware, registry, tmp_path / "workspace" / "result.xlsx"


def _mark_trained(
    registry: CollectorTrainingRegistry,
    *,
    page_path: str = "meituan/美食/商家",
    page_visit_id: str = "visit-a",
) -> None:
    registry.mark_trained(
        page_visit_id,
        page_path,
        merchant_profile={"name": "商家A", "distance": "1km"},
        script_fingerprint="script-a",
        restoration={"restored": True},
    )


def test_missing_alias_is_silent_and_breaks_empty_swipe_evidence(tmp_path):
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)
    request = _request("swipe_up")
    request.state[MERCHANT_COLLECTION_DONE] = True
    request.state[COLLECTION_CONSECUTIVE_EMPTY_SWIPES] = 2
    message = _tool_message("swipe_up")
    message.artifact["page_snapshot"]["aliases"] = {}
    result = middleware.wrap_tool_call(request, lambda _: message)
    assert _message(result) is message
    assert registry.is_trained("visit-a", request.state[PAGE_CONTEXT_PATH])
    _apply_state(request.state, result)
    assert request.state[MERCHANT_COLLECTION_DONE] is False
    assert request.state[COLLECTION_CONSECUTIVE_EMPTY_SWIPES] == 0
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_does_not_collect_before_training(tmp_path: Path, monkeypatch) -> None:
    middleware, _registry, workbook_path = _middleware(tmp_path)
    def unexpected_run(*args):
        raise AssertionError("Untrained scripts must not run")
    monkeypatch.setattr(middleware.mechanism, "run_page_script", unexpected_run)

    result = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up"))
    payload = _payload(result)

    assert "page_snapshot" in _message(result).artifact
    assert "xml" not in payload

    assert payload == {"summary": "cup"}
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_persists_swipe_items_and_deduplicates(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    first = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up", "cup"))
    second = middleware.wrap_tool_call(_request("swipe_down"), lambda _request: _tool_message("swipe_down", "cup"))

    first_payload = _payload(first)
    second_payload = _payload(second)

    assert first_payload == second_payload == {"summary": "cup"}
    assert first.update[COLLECTION_LATEST_NEW_COUNT] == 1
    assert second.update[COLLECTION_LATEST_NEW_COUNT] == 0
    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"], ["cup", "10"]]


def test_long_swipe_uses_the_same_collection_persistence_path(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    result = middleware.wrap_tool_call(
        _request("swipe_up_long"),
        lambda _request: _tool_message("swipe_up_long", "cup"),
    )

    assert _payload(result) == {"summary": "cup"}
    assert result.update[COLLECTION_LATEST_NEW_COUNT] == 1
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"], ["cup", "10"]]


def test_is_done_requires_three_consecutive_empty_swipes(tmp_path: Path) -> None:
    middleware, registry, _workbook_path = _middleware(tmp_path)
    _mark_trained(registry)
    runtime = SimpleNamespace(state=_request("is_done").state, tool_call_id="is-done-1")

    initial = middleware.is_done(runtime)
    first_swipe = middleware.wrap_tool_call(
        _request("swipe_up"),
        lambda _request: _tool_message("swipe_up", "cup"),
    )
    _apply_state(runtime.state, first_swipe)
    after_new_item = middleware.is_done(runtime)
    empty_checks = []
    for _ in range(3):
        duplicate_request = _request("swipe_up")
        duplicate_request.state.update(runtime.state)
        duplicate_swipe = middleware.wrap_tool_call(
            duplicate_request,
            lambda _request: _tool_message("swipe_up", "cup"),
        )
        _apply_state(runtime.state, duplicate_swipe)
        empty_checks.append(middleware.is_done(runtime))

    assert _payload(initial) == {
        "ok": True,
        "done": False,
        "initialized": False,
        "latest_new_count": 0,
        "consecutive_empty_swipes": 0,
        "page_path": "meituan/美食/商家",
        "page_visit_id": "visit-a",
    }
    assert _payload(after_new_item)["initialized"] is True
    assert _payload(after_new_item)["latest_new_count"] == 1
    assert _payload(after_new_item)["done"] is False
    assert [_payload(result)["consecutive_empty_swipes"] for result in empty_checks] == [1, 2, 3]
    assert [_payload(result)["done"] for result in empty_checks] == [False, False, True]
    assert empty_checks[-1].update[MERCHANT_COLLECTION_DONE] is True


def test_new_items_reset_the_consecutive_empty_swipe_count(tmp_path: Path) -> None:
    middleware, registry, _workbook_path = _middleware(tmp_path)
    _mark_trained(registry)
    state = _request("swipe_up").state

    for content in ("cup", "cup", "cup"):
        request = _request("swipe_up")
        request.state.update(state)
        result = middleware.wrap_tool_call(
            request,
            lambda _request, value=content: _tool_message("swipe_up", value),
        )
        _apply_state(state, result)
    assert state[COLLECTION_CONSECUTIVE_EMPTY_SWIPES] == 2

    request = _request("swipe_up")
    request.state.update(state)
    result = middleware.wrap_tool_call(
        request,
        lambda _request: _tool_message("swipe_up", "plate"),
    )
    _apply_state(state, result)

    assert state[COLLECTION_LATEST_NEW_COUNT] == 1
    assert state[COLLECTION_CONSECUTIVE_EMPTY_SWIPES] == 0


def test_non_swipe_tools_do_not_overwrite_collection_progress(tmp_path: Path) -> None:
    middleware, registry, _workbook_path = _middleware(tmp_path)
    _mark_trained(registry)
    runtime = SimpleNamespace(state=_request("is_done").state, tool_call_id="is-done-1")
    swipe_result = middleware.wrap_tool_call(
        _request("swipe_up"),
        lambda _request: _tool_message("swipe_up", "cup"),
    )
    _apply_state(runtime.state, swipe_result)

    for tool_name in ("page_view", "tap_in_page"):
        middleware.wrap_tool_call(
            _request(tool_name),
            lambda _request, name=tool_name: _tool_message(name, "cup"),
        )

    assert _payload(middleware.is_done(runtime))["latest_new_count"] == 1
    assert _payload(middleware.is_done(runtime))["done"] is False


def test_is_done_is_isolated_by_page_visit_and_rejects_non_merchant_page(tmp_path: Path) -> None:
    middleware, registry, _workbook_path = _middleware(tmp_path)
    _mark_trained(registry)
    _mark_trained(registry, page_visit_id="visit-b")
    middleware.wrap_tool_call(
        _request("swipe_up"),
        lambda _request: _tool_message("swipe_up", "cup"),
    )

    visit_b = middleware.is_done(
        SimpleNamespace(state=_request("is_done", page_visit_id="visit-b").state, tool_call_id="done-b")
    )
    list_page = middleware.is_done(
        SimpleNamespace(
            state=_request(
                "is_done",
                page_path="meituan/美食",
                page_visit_id="visit-list",
            ).state,
            tool_call_id="done-list",
        )
    )

    assert _payload(visit_b)["ok"] is True
    assert _payload(visit_b)["initialized"] is False
    assert _payload(visit_b)["done"] is False
    assert _payload(list_page)["ok"] is False
    assert _payload(list_page)["error_type"] == "is_done_not_available"
    assert _payload(list_page)["done"] is False


def test_is_done_tool_hides_tool_runtime_argument(tmp_path: Path) -> None:
    middleware, _registry, _workbook_path = _middleware(tmp_path)
    tools = middleware.create_tools()

    assert [tool.name for tool in tools] == ["is_done"]
    schema = tools[0].tool_call_schema.model_json_schema()
    assert "runtime" not in schema.get("properties", {})


def test_prepare_collector_initializes_merchant_sheet_from_registry(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    result = middleware.wrap_tool_call(
        _request("prepare_collector"),
        lambda _request: _tool_message(
            "prepare_collector",
            '{"ok": true, "trained": true, "page_visit_id": "visit-a"}',
        ),
    )
    payload = _payload(result)

    assert payload == {"ok": True, "trained": True, "page_visit_id": "visit-a"}
    assert result.update[COLLECTION_VISIT_SUMMARIES] == {
        "visit-a": {
            "page_path": "meituan/美食/商家",
            "merchant_profile": {"name": "商家A", "distance": "1km"},
            "collected_items": 0,
            "is_leaf": True,
        }
    }
    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]


def test_collection_summary_accumulates_new_items_for_each_page_visit(tmp_path: Path) -> None:
    middleware, registry, _workbook_path = _middleware(tmp_path)
    _mark_trained(registry)
    state = _request("prepare_collector").state
    prepared_request = _request("prepare_collector")
    prepared_request.state = state
    prepared = middleware.wrap_tool_call(
        prepared_request,
        lambda _request: _tool_message("prepare_collector", '{"trained": true}'),
    )
    _apply_state(state, prepared)

    for content in ("cup", "plate", "cup"):
        request = _request("swipe_up")
        request.state = state
        result = middleware.wrap_tool_call(
            request,
            lambda _request, value=content: _tool_message("swipe_up", value),
        )
        _apply_state(state, result)

    assert state[COLLECTION_VISIT_SUMMARIES]["visit-a"] == {
        "page_path": "meituan/美食/商家",
        "merchant_profile": {"name": "商家A", "distance": "1km"},
        "collected_items": 2,
        "is_leaf": True,
    }


def test_collection_summary_keeps_separate_visit_totals(tmp_path: Path) -> None:
    middleware, registry, _workbook_path = _middleware(tmp_path)
    _mark_trained(registry)
    _mark_trained(registry, page_visit_id="visit-b")
    state = {}

    for visit_id, content in (("visit-a", "cup"), ("visit-b", "plate")):
        request = _request("swipe_up", page_visit_id=visit_id)
        request.state.update(state)
        result = middleware.wrap_tool_call(
            request,
            lambda _request, value=content: _tool_message("swipe_up", value),
        )
        _apply_state(state, result)

    summaries = state[COLLECTION_VISIT_SUMMARIES]
    assert summaries["visit-a"]["collected_items"] == 1
    assert summaries["visit-b"]["collected_items"] == 1


def test_wrap_tool_call_initializes_sheet_but_does_not_append_for_same_page_reads(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    for tool_name in ("tap_in_page", "page_view"):
        result = middleware.wrap_tool_call(_request(tool_name), lambda _request: _tool_message(tool_name, "cup"))
        payload = _payload(result)
        assert payload == {"summary": "cup"}

    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"]]


def test_navigation_xml_does_not_run_page_script(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    for tool_name in ("navigate_to", "navigate_back"):
        result = middleware.wrap_tool_call(_request(tool_name), lambda _request: _tool_message(tool_name, "cup"))
    assert json.loads(_message(result).content) == {"summary": "cup"}

    assert _read_xlsx(workbook_path) == []


def test_overlay_close_xml_does_not_run_old_page_script(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    request = _request("tap_in_page", overlay=True)
    request.tool_call["args"]["purpose"] = "dismiss_overlay"
    result = middleware.wrap_tool_call(
        request,
        lambda _request: _tool_message("tap_in_page", "new-page-xml"),
    )

    assert json.loads(_message(result).content) == {"summary": "new-page-xml"}
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_uses_tool_call_name_not_result_content_to_trigger(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    result = middleware.wrap_tool_call(
        _request("think"),
        lambda _request: _tool_message("think", "this content mentions swipe_up and merchant data"),
    )

    assert json.loads(_message(result).content) == {"summary": "this content mentions swipe_up and merchant data"}
    assert _read_xlsx(workbook_path) == []


def test_script_error_is_silent_without_persisting(tmp_path: Path, caplog) -> None:
    page_dir = tmp_path / "pages" / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.agent.py").write_text("def run(view):\n    raise ValueError('bad parse')\n", encoding="utf-8")
    (page_dir / "COLLECT.md").write_text('{"required_aliases":["1"]}\nProduct region.')
    mechanism = PageMechanism(tmp_path / "pages")
    registry = CollectorTrainingRegistry()
    _mark_trained(registry)
    middleware = CollectionPersistenceMiddleware(
        mechanism=mechanism,
        training_registry=registry,
        workbook_sink=CollectionWorkbookSink(ContentProvider(tmp_path / "workspace")),
    )

    result = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up"))
    payload = _payload(result)

    assert payload == {"summary": "cup"}
    assert _message(result).status == "success"
    assert "bad parse" in caplog.text
    assert result.update[MERCHANT_COLLECTION_DONE] is False
    assert _read_xlsx(tmp_path / "workspace" / "result.xlsx") == []


def test_async_collection_preserves_original_message(tmp_path):
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)
    message = _tool_message("swipe_up")

    async def handler(request):
        return message

    result = asyncio.run(middleware.awrap_tool_call(_request("swipe_up"), handler))
    assert _message(result) is message
    assert result.update[COLLECTION_LATEST_NEW_COUNT] == 1
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"], ["cup", "10"]]


def test_persistence_failure_is_silent_and_cannot_confirm_completion(tmp_path, monkeypatch, caplog):
    middleware, registry, _ = _middleware(tmp_path)
    _mark_trained(registry)
    request = _request("swipe_up")
    message = _tool_message("swipe_up")

    def fail(**kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(middleware.workbook_sink, "persist_run_result", fail)
    result = middleware.wrap_tool_call(request, lambda _: message)
    assert _message(result) is message
    assert "disk unavailable" in caplog.text
    _apply_state(request.state, result)
    done = middleware.is_done(SimpleNamespace(state=request.state, tool_call_id="done"))
    assert _payload(done)["done"] is False
