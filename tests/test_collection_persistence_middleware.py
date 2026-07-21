from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from middleware.collection_persistence import CollectionPersistenceMiddleware
from middleware.page_context import PAGE_CONTEXT_OVERLAY, PAGE_CONTEXT_PATH, PAGE_VISIT_ID
from middleware.probe_state import (
    COLLECTION_INITIALIZED,
    COLLECTION_LATEST_NEW_COUNT,
    COLLECTION_PAGE_VISIT_ID,
    MERCHANT_COLLECTION_DONE,
)
from page_mechanism import CollectorTrainingRegistry, PageMechanism
from tools.content_provider import CollectionWorkbookSink, ContentProvider, _read_xlsx


def _request(
    tool_name: str,
    *,
    overlay: bool = False,
    page_path: str = "meituan/美食/商家",
    page_visit_id: str = "visit-a",
) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool_name, "id": f"{tool_name}-1", "args": {}},
        state={
            PAGE_CONTEXT_PATH: page_path,
            PAGE_CONTEXT_OVERLAY: overlay,
            PAGE_VISIT_ID: page_visit_id,
        },
    )


def _tool_message(tool_name: str, content: str = "cup") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=f"{tool_name}-1", name=tool_name)


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
        "def run(xml: str):\n"
        "    return {\n"
        "        'items': [{'name': xml, 'price': '10'}],\n"
        "    }\n",
        encoding="utf-8",
    )
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


def test_wrap_tool_call_does_not_persist_before_collector_training(tmp_path: Path) -> None:
    middleware, _registry, workbook_path = _middleware(tmp_path)

    result = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up"))
    payload = _payload(result)

    assert payload["collection"]["ok"] is True
    assert payload["persistence"]["reason"] == "collector_not_trained"
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_persists_swipe_items_and_deduplicates(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    first = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up", "cup"))
    second = middleware.wrap_tool_call(_request("swipe_down"), lambda _request: _tool_message("swipe_down", "cup"))

    first_payload = _payload(first)
    second_payload = _payload(second)

    assert first_payload["persistence"]["appended_items"] == 1
    assert second_payload["persistence"]["appended_items"] == 0
    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"], ["cup", "10"]]


def test_is_done_uses_latest_persisted_item_delta(tmp_path: Path) -> None:
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
    second_request = _request("swipe_up")
    second_request.state.update(runtime.state)
    second_swipe = middleware.wrap_tool_call(
        second_request,
        lambda _request: _tool_message("swipe_up", "cup"),
    )
    _apply_state(runtime.state, second_swipe)
    after_duplicate = middleware.is_done(runtime)

    assert _payload(initial) == {
        "ok": True,
        "done": False,
        "initialized": False,
        "latest_new_count": 0,
        "page_path": "meituan/美食/商家",
        "page_visit_id": "visit-a",
    }
    assert _payload(after_new_item)["initialized"] is True
    assert _payload(after_new_item)["latest_new_count"] == 1
    assert _payload(after_new_item)["done"] is False
    assert _payload(after_duplicate)["latest_new_count"] == 0
    assert _payload(after_duplicate)["done"] is True
    assert after_duplicate.update[MERCHANT_COLLECTION_DONE] is True


def test_non_swipe_tools_do_not_overwrite_collection_progress(tmp_path: Path) -> None:
    middleware, registry, _workbook_path = _middleware(tmp_path)
    _mark_trained(registry)
    runtime = SimpleNamespace(state=_request("is_done").state, tool_call_id="is-done-1")
    swipe_result = middleware.wrap_tool_call(
        _request("swipe_up"),
        lambda _request: _tool_message("swipe_up", "cup"),
    )
    _apply_state(runtime.state, swipe_result)

    for tool_name in ("uiautomate", "tap_in_page"):
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

    assert payload["persistence"]["ok"] is True
    assert payload["persistence"]["merchant_sheet"] == "商家A"
    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]


def test_wrap_tool_call_initializes_sheet_but_does_not_append_for_same_page_reads(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    for tool_name in ("tap_in_page", "uiautomate"):
        result = middleware.wrap_tool_call(_request(tool_name), lambda _request: _tool_message(tool_name, "cup"))
        payload = _payload(result)
        assert payload["persistence"]["appended_items"] == 0

    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"]]


def test_navigation_xml_does_not_run_page_script(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    for tool_name in ("navigate_to", "navigate_back"):
        result = middleware.wrap_tool_call(_request(tool_name), lambda _request: _tool_message(tool_name, "cup"))
        assert _message(result).content == "cup"

    assert _read_xlsx(workbook_path) == []


def test_overlay_close_xml_does_not_run_old_page_script(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    result = middleware.wrap_tool_call(
        _request("tap_in_page", overlay=True),
        lambda _request: _tool_message("tap_in_page", "new-page-xml"),
    )

    assert _message(result).content == "new-page-xml"
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_uses_tool_call_name_not_result_content_to_trigger(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    result = middleware.wrap_tool_call(
        _request("think"),
        lambda _request: _tool_message("think", "this content mentions swipe_up and merchant data"),
    )

    assert _message(result).content == "this content mentions swipe_up and merchant data"
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_returns_structured_collection_error_without_persisting(tmp_path: Path) -> None:
    page_dir = tmp_path / "pages" / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.agent.py").write_text("def run(xml: str):\n    raise ValueError('bad parse')\n", encoding="utf-8")
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

    assert payload["collection"]["ok"] is False
    assert payload["collection"]["error_type"] == "ValueError"
    assert payload["persistence"]["reason"] == "collection_not_ok"
