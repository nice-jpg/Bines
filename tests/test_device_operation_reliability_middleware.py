from __future__ import annotations

from src.conductor.state import read_conductor_state, merge_fields

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

from src.tools.collection.service import CollectionService

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from src.device.services.page.bridge import PageBridge
from src.device.services.page.similarity import PageSimilarityResult
from src.device.types.results import make_error_result
from src.strategy.general.reliability import DeviceOperationReliabilityMiddleware
from src.strategy.merchant.persistence import CollectionPersistenceMiddleware
from src.tools.device.effect import (
    NAVIGATION_PAGE_CHANGE_POLICY,
    OPERATION_EFFECT_POLICY_METADATA_KEY,
    SCROLL_PAGE_CHANGE_POLICY,
)

XML = '<hierarchy><node bounds="[0,0][100,80]" text="page"/></hierarchy>'


class FakeEventHub:
    def __init__(self, result=XML) -> None:
        self.result = result
        self.calls = 0
        self.page_bridge = PageBridge(self.page_view)

    def page_view(self):
        self.calls += 1
        return self.result


class FixedEvaluator:
    def __init__(self, score: float) -> None:
        self.score = score

    def compare(self, before_xml: str, after_xml: str) -> PageSimilarityResult:
        return PageSimilarityResult(self.score, self.score, self.score)


def _tool(name: str, policy=None) -> StructuredTool:
    metadata = {} if policy is None else {OPERATION_EFFECT_POLICY_METADATA_KEY: policy}
    return StructuredTool.from_function(func=lambda: XML, name=name, description=name, metadata=metadata)


def _request(tool: StructuredTool):
    return SimpleNamespace(
        tool=tool,
        tool_call={"name": tool.name, "id": "call-1", "args": {}},
        state=read_conductor_state({"page_context_path": "meituan/外卖", "page_visit_id": "visit-1", "page_visit": {"kind": "general"}}),
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "thread-1"}}),
    )


def _message(name: str) -> ToolMessage:
    return ToolMessage(content='{"summary": "page"}', artifact={"page_xml": XML}, name=name, tool_call_id="call-1")


def _middleware(score: float, *, event_result=XML):
    hub = FakeEventHub(event_result)
    middleware = DeviceOperationReliabilityMiddleware(event_hub=hub)
    middleware.evaluator = FixedEvaluator(score)
    middleware.logger = SimpleNamespace(log=lambda *args, **kwargs: None)
    return middleware, hub


def test_tool_without_policy_is_unchanged_and_does_not_capture_page() -> None:
    middleware, hub = _middleware(1.0)
    expected = _message("page_view")
    result = middleware.wrap_tool_call(_request(_tool("page_view")), lambda _: expected)
    assert result is expected
    assert hub.calls == 0


def test_reliability_uses_full_xml_not_search_hint_presence():
    for score, expected_status in ((0.4, "success"), (1.0, "error")):
        middleware, _ = _middleware(score)
        compared = []

        def compare(before, after):
            compared.append((before, after))
            return PageSimilarityResult(score, score, score)

        middleware.evaluator.compare = compare
        message = _message("swipe_up")
        message.content = json.dumps({"kind": "page_change_hint", "regions": []})
        result = middleware.wrap_tool_call(
            _request(_tool("swipe_up", SCROLL_PAGE_CHANGE_POLICY)), lambda _: message,
        )
        assert (result.status if isinstance(result, ToolMessage) else result.update["messages"][0].status) == "success"
        assert compared == [(XML, XML)]
        if expected_status == "success":
            assert result is message


def test_navigation_accepts_only_similarity_below_half() -> None:
    accepted, _ = _middleware(0.499)
    expected = _message("navigate_to")
    assert accepted.wrap_tool_call(
        _request(_tool("navigate_to", NAVIGATION_PAGE_CHANGE_POLICY)), lambda _: expected
    ) is expected

    rejected, _ = _middleware(0.5)
    result = rejected.wrap_tool_call(
        _request(_tool("navigate_to", NAVIGATION_PAGE_CHANGE_POLICY)), lambda _: expected
    )
    assert result.update["messages"][0] is expected
    assert "50.0%" in result.update["messages"][-1].content


def test_scroll_boundaries_are_inclusive() -> None:
    for score in (0.05, 0.95):
        middleware, _ = _middleware(score)
        expected = _message("swipe_up")
        result = middleware.wrap_tool_call(
            _request(_tool("swipe_up", SCROLL_PAGE_CHANGE_POLICY)), lambda _: expected
        )
        assert result is expected

    for score in (0.049, 0.951):
        middleware, _ = _middleware(score)
        result = middleware.wrap_tool_call(
            _request(_tool("swipe_down", SCROLL_PAGE_CHANGE_POLICY)), lambda _: _message("swipe_down")
        )
        assert result.update["messages"][0].status == "success"


def test_precheck_failure_warns_after_device_operation() -> None:
    middleware, _ = _middleware(0.5, event_result=make_error_result("capture_failed", "failed"))
    handler_calls = []
    result = middleware.wrap_tool_call(
        _request(_tool("swipe_up", SCROLL_PAGE_CHANGE_POLICY)),
        lambda _: handler_calls.append(True) or _message("swipe_up"),
    )
    assert handler_calls == [True]
    assert "无法比较" in result.update["messages"][-1].content


def test_unverifiable_post_result_exposes_no_page_details() -> None:
    middleware, _ = _middleware(0.5)
    from src.device.services.page.similarity import PageSimilarityEvaluator

    middleware.evaluator = PageSimilarityEvaluator()
    result = middleware.wrap_tool_call(
        _request(_tool("swipe_up", SCROLL_PAGE_CHANGE_POLICY)),
        lambda _: ToolMessage(content="not xml", name="swipe_up", tool_call_id="call-1"),
    )
    assert result.update["messages"][0].content == "not xml"
    assert "无法比较" in result.update["messages"][-1].content


def test_async_path_uses_the_same_policy() -> None:
    middleware, hub = _middleware(0.96)

    async def handler(_):
        return _message("swipe_up")

    result = asyncio.run(
        middleware.awrap_tool_call(_request(_tool("swipe_up", SCROLL_PAGE_CHANGE_POLICY)), handler)
    )
    assert result.update["messages"][0].status == "success"
    assert hub.calls == 1


def test_warning_retains_tool_result_without_snapshot() -> None:
    class FakeMechanism:
        def __init__(self) -> None:
            self.run_calls = 0

        def run_page_script(self, path, xml):
            self.run_calls += 1
            return {"ok": True, "result": {"items": []}}

        def is_merchant_page(self, path):
            return True

    class FakeRegistry:
        def is_trained(self, visit_id, path):
            return True

    mechanism = FakeMechanism()
    persistence = CollectionPersistenceMiddleware(CollectionService(mechanism, FakeRegistry(), None, None))
    reliability, _ = _middleware(0.96)
    request = _request(_tool("swipe_up", SCROLL_PAGE_CHANGE_POLICY))

    result = persistence.wrap_tool_call(
        request,
        lambda inner: reliability.wrap_tool_call(inner, lambda _: _message("swipe_up")),
    )

    assert result.update["messages"][0].status == "success"
    assert mechanism.run_calls == 0
