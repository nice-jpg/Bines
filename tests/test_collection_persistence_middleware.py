from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage

from middleware.collection_persistence import CollectionPersistenceMiddleware
from middleware.page_context import PAGE_CONTEXT_OVERLAY, PAGE_CONTEXT_PATH, PAGE_VISIT_ID
from page_mechanism import CollectorTrainingRegistry, PageMechanism
from tools.content_provider import CollectionWorkbookSink, ContentProvider, _read_xlsx


def _request(tool_name: str, *, overlay: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        tool_call={"name": tool_name, "id": f"{tool_name}-1", "args": {}},
        state={
            PAGE_CONTEXT_PATH: "meituan/美食/商家",
            PAGE_CONTEXT_OVERLAY: overlay,
            PAGE_VISIT_ID: "visit-a",
        },
    )


def _tool_message(tool_name: str, content: str = "cup") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=f"{tool_name}-1", name=tool_name)


def _middleware(tmp_path: Path) -> tuple[CollectionPersistenceMiddleware, CollectorTrainingRegistry, Path]:
    page_dir = tmp_path / "pages" / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.py").write_text(
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


def _mark_trained(registry: CollectorTrainingRegistry) -> None:
    registry.mark_trained(
        "visit-a",
        "meituan/美食/商家",
        merchant_profile={"name": "商家A", "distance": "1km"},
        script_fingerprint="script-a",
        restoration={"restored": True},
    )


def test_wrap_tool_call_does_not_persist_before_collector_training(tmp_path: Path) -> None:
    middleware, _registry, workbook_path = _middleware(tmp_path)

    result = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up"))
    payload = json.loads(result.content)

    assert payload["collection"]["ok"] is True
    assert payload["persistence"]["reason"] == "collector_not_trained"
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_persists_swipe_items_and_deduplicates(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    first = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up", "cup"))
    second = middleware.wrap_tool_call(_request("swipe_down"), lambda _request: _tool_message("swipe_down", "cup"))

    first_payload = json.loads(first.content)
    second_payload = json.loads(second.content)

    assert first_payload["persistence"]["appended_items"] == 1
    assert second_payload["persistence"]["appended_items"] == 0
    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"], ["cup", "10"]]


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
    payload = json.loads(result.content)

    assert payload["persistence"]["ok"] is True
    assert payload["persistence"]["merchant_sheet"] == "商家A"
    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]


def test_wrap_tool_call_initializes_sheet_but_does_not_append_for_same_page_reads(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    for tool_name in ("tap_in_page", "uiautomate"):
        result = middleware.wrap_tool_call(_request(tool_name), lambda _request: _tool_message(tool_name, "cup"))
        payload = json.loads(result.content)
        assert payload["persistence"]["appended_items"] == 0

    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"]]


def test_navigation_xml_does_not_run_page_script(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    for tool_name in ("navigate_to", "navigate_back"):
        result = middleware.wrap_tool_call(_request(tool_name), lambda _request: _tool_message(tool_name, "cup"))
        assert result.content == "cup"

    assert _read_xlsx(workbook_path) == []


def test_overlay_close_xml_does_not_run_old_page_script(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    result = middleware.wrap_tool_call(
        _request("tap_in_page", overlay=True),
        lambda _request: _tool_message("tap_in_page", "new-page-xml"),
    )

    assert result.content == "new-page-xml"
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_uses_tool_call_name_not_result_content_to_trigger(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    _mark_trained(registry)

    result = middleware.wrap_tool_call(
        _request("think"),
        lambda _request: _tool_message("think", "this content mentions swipe_up and merchant data"),
    )

    assert result.content == "this content mentions swipe_up and merchant data"
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_returns_structured_collection_error_without_persisting(tmp_path: Path) -> None:
    page_dir = tmp_path / "pages" / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.py").write_text("def run(xml: str):\n    raise ValueError('bad parse')\n", encoding="utf-8")
    mechanism = PageMechanism(tmp_path / "pages")
    registry = CollectorTrainingRegistry()
    _mark_trained(registry)
    middleware = CollectionPersistenceMiddleware(
        mechanism=mechanism,
        training_registry=registry,
        workbook_sink=CollectionWorkbookSink(ContentProvider(tmp_path / "workspace")),
    )

    result = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up"))
    payload = json.loads(result.content)

    assert payload["collection"]["ok"] is False
    assert payload["collection"]["error_type"] == "ValueError"
    assert payload["persistence"]["reason"] == "collection_not_ok"
