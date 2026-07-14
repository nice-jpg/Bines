from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage

from middleware.collection_persistence import CollectionPersistenceMiddleware
from page_mechanism import CollectorTrainingRegistry, PageMechanism, PageRunHook
from tools.content_provider import CollectionWorkbookSink, ContentProvider, _read_xlsx


def _request(tool_name: str) -> SimpleNamespace:
    return SimpleNamespace(tool_call={"name": tool_name, "id": f"{tool_name}-1", "args": {}})


def _tool_message(tool_name: str, content: str = "cup") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=f"{tool_name}-1", name=tool_name)


def _middleware(tmp_path: Path) -> tuple[CollectionPersistenceMiddleware, CollectorTrainingRegistry, Path]:
    page_dir = tmp_path / "pages" / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "run.py").write_text(
        "def run(xml: str):\n"
        "    return {\n"
        "        'merchant': {'name': '商家A', 'distance': '1km'},\n"
        "        'items': [{'name': xml, 'price': '10'}],\n"
        "    }\n",
        encoding="utf-8",
    )
    mechanism = PageMechanism(tmp_path / "pages")
    registry = CollectorTrainingRegistry()
    hook = PageRunHook(mechanism)
    hook.set_current_path("meituan/美食/商家")
    sink = CollectionWorkbookSink(ContentProvider(tmp_path / "workspace"))
    middleware = CollectionPersistenceMiddleware(
        page_run_hook=hook,
        training_registry=registry,
        workbook_sink=sink,
    )
    return middleware, registry, tmp_path / "workspace" / "result.xlsx"


def test_wrap_tool_call_does_not_persist_before_collector_training(tmp_path: Path) -> None:
    middleware, _registry, workbook_path = _middleware(tmp_path)

    result = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up"))
    payload = json.loads(result.content)

    assert payload["collection"]["ok"] is True
    assert payload["persistence"]["reason"] == "collector_not_trained"
    assert _read_xlsx(workbook_path) == []


def test_wrap_tool_call_persists_swipe_items_and_deduplicates(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    registry.mark_trained("meituan/美食/商家")

    first = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up", "cup"))
    second = middleware.wrap_tool_call(_request("swipe_down"), lambda _request: _tool_message("swipe_down", "cup"))

    first_payload = json.loads(first.content)
    second_payload = json.loads(second.content)

    assert first_payload["persistence"]["appended_items"] == 1
    assert second_payload["persistence"]["appended_items"] == 0
    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"], ["cup", "10"]]


def test_wrap_tool_call_initializes_sheet_but_does_not_append_for_tap_uiautomate_or_back(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    registry.mark_trained("meituan/美食/商家")

    for tool_name in ("tap", "uiautomate", "swipe_back"):
        result = middleware.wrap_tool_call(_request(tool_name), lambda _request: _tool_message(tool_name, "cup"))
        payload = json.loads(result.content)
        assert payload["persistence"]["appended_items"] == 0

    assert _read_xlsx(workbook_path) == [["name", "distance"], ["商家A", "1km"]]
    assert _read_xlsx(workbook_path, "商家A") == [["name", "price"]]


def test_wrap_tool_call_uses_tool_call_name_not_result_content_to_trigger(tmp_path: Path) -> None:
    middleware, registry, workbook_path = _middleware(tmp_path)
    registry.mark_trained("meituan/美食/商家")

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
    registry.mark_trained("meituan/美食/商家")
    hook = PageRunHook(mechanism)
    hook.set_current_path("meituan/美食/商家")
    middleware = CollectionPersistenceMiddleware(
        page_run_hook=hook,
        training_registry=registry,
        workbook_sink=CollectionWorkbookSink(ContentProvider(tmp_path / "workspace")),
    )

    result = middleware.wrap_tool_call(_request("swipe_up"), lambda _request: _tool_message("swipe_up"))
    payload = json.loads(result.content)

    assert payload["collection"]["ok"] is False
    assert payload["collection"]["error_type"] == "ValueError"
    assert payload["persistence"]["reason"] == "collection_not_ok"
