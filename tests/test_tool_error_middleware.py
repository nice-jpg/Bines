from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from langchain_core.messages import ToolMessage
from langgraph.errors import GraphInterrupt
from middleware.tool_error import ToolErrorMiddleware, make_tool_error_message


class ToolErrorMiddlewareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.middleware = ToolErrorMiddleware()
        self.request = SimpleNamespace(
            tool_call={
                "id": "call-1",
                "name": "tap",
                "args": {"x": 100, "y": 200},
            },
            tool=SimpleNamespace(name="tap"),
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
        result = make_tool_error_message(SimpleNamespace(), RuntimeError())

        self.assertEqual(result.name, "unknown_tool")
        self.assertEqual(result.tool_call_id, "")
        self.assertIn("No error message was provided.", result.content)


if __name__ == "__main__":
    unittest.main()
