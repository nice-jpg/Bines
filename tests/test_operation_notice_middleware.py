from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool

from middleware.operation_notice import OperationNoticeMiddleware
from middleware.page_context import PAGE_CONTEXT_ACTIVE, PAGE_CONTEXT_PATH, PageContextMiddleware
from tools.operation_policy import (
    EXTERNAL_OPERATION_POLICY_METADATA_KEY,
    OPERATION_NOTICE_SERVICE_METADATA_KEY,
    ExternalOperationPolicy,
)


class FakeNoticeService:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def notify_user(self, operation, current_path, reason="", next_page="", next_path="") -> str:
        self.calls.append(
            {
                "operation": operation,
                "current_path": current_path,
                "reason": reason,
                "next_page": next_page,
                "next_path": next_path,
            }
        )
        return "recorded"


def _tool(name: str, metadata: dict) -> StructuredTool:
    return StructuredTool.from_function(
        func=lambda: "ok",
        name=name,
        description=name,
        metadata=metadata,
    )


def _middleware() -> tuple[OperationNoticeMiddleware, FakeNoticeService]:
    service = FakeNoticeService()
    carrier = _tool("notice_service", {OPERATION_NOTICE_SERVICE_METADATA_KEY: service})
    return OperationNoticeMiddleware([carrier]), service


def _request(tool: StructuredTool, *, state: dict | None = None, args: dict | None = None):
    return SimpleNamespace(
        tool=tool,
        tool_call={"name": tool.name, "id": "call-1", "args": args or {}},
        state=state or {PAGE_CONTEXT_PATH: "meituan"},
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "thread-1"}}),
    )


def test_external_operation_is_announced_immediately_before_handler() -> None:
    middleware, service = _middleware()
    tool = _tool(
        "navigate_to",
        {
            EXTERNAL_OPERATION_POLICY_METADATA_KEY: ExternalOperationPolicy(
                "Navigate to the declared page.",
                next_path_argument="next_path",
            )
        },
    )
    order: list[str] = []

    original_notify = service.notify_user

    def notify(*args, **kwargs):
        order.append("notice")
        return original_notify(*args, **kwargs)

    service.notify_user = notify
    result = middleware.wrap_tool_call(
        _request(tool, args={"next_path": "meituan/外卖"}),
        lambda _: order.append("handler") or ToolMessage(content="ok", tool_call_id="call-1"),
    )

    assert result.content == "ok"
    assert order == ["notice", "handler"]
    assert service.calls == [
        {
            "operation": "navigate_to",
            "current_path": "meituan",
            "reason": "Navigate to the declared page.",
            "next_page": "外卖",
            "next_path": "meituan/外卖",
        }
    ]


def test_dangerous_justification_supplies_notice_reason() -> None:
    middleware, service = _middleware()
    tool = _tool(
        "run_package",
        {EXTERNAL_OPERATION_POLICY_METADATA_KEY: ExternalOperationPolicy("Launch application.")},
    )
    middleware.wrap_tool_call(
        _request(tool, args={"justification": {"reason": "Start the configured probe."}}),
        lambda _: ToolMessage(content="ok", tool_call_id="call-1"),
    )

    assert service.calls[0]["reason"] == "Start the configured probe."


def test_page_guard_rejection_does_not_emit_notice() -> None:
    notice_middleware, service = _middleware()
    page_middleware = PageContextMiddleware()
    tool = _tool(
        "page_view",
        {EXTERNAL_OPERATION_POLICY_METADATA_KEY: ExternalOperationPolicy("Read the current page.")},
    )
    request = _request(
        tool,
        state={PAGE_CONTEXT_ACTIVE: True, PAGE_CONTEXT_PATH: ""},
    )

    result = page_middleware.wrap_tool_call(
        request,
        lambda inner_request: notice_middleware.wrap_tool_call(
            inner_request,
            lambda _: ToolMessage(content="unexpected", tool_call_id="call-1"),
        ),
    )

    assert result.status == "error"
    assert service.calls == []


def test_async_external_operation_uses_same_notice_path() -> None:
    middleware, service = _middleware()
    tool = _tool(
        "swipe_up",
        {EXTERNAL_OPERATION_POLICY_METADATA_KEY: ExternalOperationPolicy("Scroll the current page.")},
    )

    async def handler(_):
        return ToolMessage(content="ok", tool_call_id="call-1")

    result = asyncio.run(middleware.awrap_tool_call(_request(tool), handler))

    assert result.content == "ok"
    assert service.calls[0]["operation"] == "swipe_up"
