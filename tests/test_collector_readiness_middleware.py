from __future__ import annotations

import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage

from middleware.collector_readiness import CollectorReadinessMiddleware
from middleware.page_context import PAGE_CONTEXT_OVERLAY, PAGE_CONTEXT_PATH, PAGE_VISIT_ID
from page_mechanism import CollectorTrainingRegistry, PageMechanism


@dataclass
class FakeModelRequest:
    state: dict
    tools: list

    def override(self, **kwargs):
        return replace(self, **kwargs)


def _middleware(tmp_path: Path):
    page_dir = tmp_path / "pages" / "meituan" / "美食" / "商家"
    page_dir.mkdir(parents=True)
    (page_dir / "PAGE.md").write_text("merchant", encoding="utf-8")
    (page_dir / "task.md").write_text("task", encoding="utf-8")
    mechanism = PageMechanism(tmp_path / "pages")
    registry = CollectorTrainingRegistry()
    return CollectorReadinessMiddleware(mechanism=mechanism, training_registry=registry), registry


def _state(visit_id: str = "visit-a", *, overlay: bool = False) -> dict:
    return {
        PAGE_CONTEXT_PATH: "meituan/美食/商家",
        PAGE_VISIT_ID: visit_id,
        PAGE_CONTEXT_OVERLAY: overlay,
    }


def _tool_request(name: str, state: dict):
    return SimpleNamespace(
        tool_call={"name": name, "id": f"{name}-1", "args": {}},
        tool=None,
        state=state,
    )


def test_untrained_merchant_visit_only_exposes_collector_control_tools(tmp_path: Path) -> None:
    middleware, _registry = _middleware(tmp_path)
    tools = [SimpleNamespace(name=name) for name in ("prepare_collector", "swipe_up", "tap_in_page", "navigate_back")]

    prepared = middleware._prepare_model_request(FakeModelRequest(state=_state(), tools=tools))

    assert [tool.name for tool in prepared.tools] == ["prepare_collector", "navigate_back"]


def test_untrained_visit_rejects_page_operation_but_overlay_close_is_allowed(tmp_path: Path) -> None:
    middleware, _registry = _middleware(tmp_path)
    called = []

    rejected = middleware.wrap_tool_call(
        _tool_request("swipe_up", _state()),
        lambda _: called.append("swipe") or ToolMessage(content="xml", tool_call_id="swipe_up-1"),
    )
    allowed = middleware.wrap_tool_call(
        _tool_request("tap_in_page", _state(overlay=True)),
        lambda _: called.append("close") or ToolMessage(content="xml", tool_call_id="tap_in_page-1"),
    )

    assert json.loads(rejected.content)["error_type"] == "collector_training_required"
    assert called == ["close"]
    assert allowed.content == "xml"


def test_training_is_scoped_to_visit_id_even_when_page_path_matches(tmp_path: Path) -> None:
    middleware, registry = _middleware(tmp_path)
    registry.mark_trained(
        "visit-a",
        "meituan/美食/商家",
        merchant_profile={"name": "商家A"},
        restoration={"restored": True},
    )

    first = middleware.wrap_tool_call(
        _tool_request("swipe_up", _state("visit-a")),
        lambda _: ToolMessage(content="first", tool_call_id="swipe_up-1"),
    )
    second = middleware.wrap_tool_call(
        _tool_request("swipe_up", _state("visit-b")),
        lambda _: ToolMessage(content="second", tool_call_id="swipe_up-1"),
    )

    assert first.content == "first"
    assert json.loads(second.content)["error_type"] == "collector_training_required"
