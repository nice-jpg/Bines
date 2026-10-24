from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langgraph.errors import GraphInterrupt
from src.agent_runtime.middleware.tool_error import ToolErrorMiddleware, make_tool_error_message
from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime
from src.tools.shared.factory import create_tool


class ToolErrorMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.middleware = ToolErrorMiddleware()
        self.request = SimpleNamespace(
            tool_call={
                "id": "call-1",
                "name": "tap",
                "args": {"x": 100, "y": 200},
            },
            tool=None,
            state={},
        )

    def test_successful_sync_tool_result_is_unchanged(self) -> None:
        expected = ToolMessage(content="ok", tool_call_id="call-1", name="tap")

        result = self.middleware.wrap_tool_call(self.request, lambda _request: expected)

        self.assertIs(result, expected)

    def test_sync_exception_becomes_model_visible_error_result(self) -> None:
        def fail(_request):
            raise ValueError("coordinate is outside the screen")

        result = self.middleware.wrap_tool_call(self.request, fail)

        self.assertIsInstance(result, ToolMessage)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.tool_call_id, "call-1")
        self.assertEqual(result.name, "tap")
        self.assertIn("ValueError", result.content)
        self.assertIn("coordinate is outside the screen", result.content)
        self.assertEqual(result.additional_kwargs["error_type"], "ValueError")

    def test_async_exception_becomes_model_visible_error_result(self) -> None:
        async def fail(_request):
            raise RuntimeError("device disconnected")

        result = asyncio.run(self.middleware.awrap_tool_call(self.request, fail))

        self.assertEqual(result.status, "error")
        self.assertIn("RuntimeError", result.content)
        self.assertIn("device disconnected", result.content)

    def test_graph_interrupt_is_not_converted_to_tool_error(self) -> None:
        def interrupt(_request):
            raise GraphInterrupt()

        with self.assertRaises(GraphInterrupt):
            self.middleware.wrap_tool_call(self.request, interrupt)

    def test_error_message_falls_back_when_tool_metadata_is_missing(self) -> None:
        result = make_tool_error_message(
            SimpleNamespace(
                tool_call={"id": "", "name": "", "args": {}},
                tool=None,
                state={},
            ),
            RuntimeError(),
        )

        self.assertEqual(result.name, "unknown_tool")
        self.assertEqual(result.tool_call_id, "")
        self.assertIn("No error message was provided.", result.content)

    def test_public_schema_does_not_require_injected_runtime(self) -> None:
        def observe(label: str, runtime: ToolRuntime) -> str:
            """Observe using hidden runtime."""
            return label

        self.request.tool = create_tool(observe)
        self.request.tool_call["args"] = {"label": "current"}
        expected = ToolMessage(content="ok", tool_call_id="call-1")
        self.assertIs(self.middleware.wrap_tool_call(self.request, lambda _: expected), expected)

    def test_invalid_arguments_never_enter_business_middleware_sync_or_async(self) -> None:
        def tap(x: int) -> str:
            """Tap a coordinate."""
            return "ok"

        self.request.tool = create_tool(tap)
        self.request.tool_call["args"] = {"x": "not a coordinate"}

        def business(_):
            self.fail("Invalid arguments reached the business layer")

        async def async_business(_):
            self.fail("Invalid arguments reached the async business layer")

        results = [
            self.middleware.wrap_tool_call(self.request, business),
            asyncio.run(self.middleware.awrap_tool_call(self.request, async_business)),
        ]
        for result in results:
            self.assertEqual(result.status, "error")
            self.assertEqual(result.additional_kwargs["error_type"], "ValidationError")


if __name__ == "__main__":
    unittest.main()
