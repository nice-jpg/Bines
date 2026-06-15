from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

_ORIGINAL_LANGCHAIN_MODULES = {
    name: sys.modules.get(name)
    for name in (
        "langchain",
        "langchain.agents",
        "langchain.agents.middleware",
        "langchain.agents.middleware.types",
    )
}

if "langchain.agents.middleware.types" not in sys.modules:
    langchain = types.ModuleType("langchain")
    langchain_agents = types.ModuleType("langchain.agents")
    langchain_agents_middleware = types.ModuleType("langchain.agents.middleware")
    langchain_agents_middleware_types = types.ModuleType("langchain.agents.middleware.types")

    class AgentMiddleware:
        pass

    langchain_agents_middleware_types.AgentMiddleware = AgentMiddleware
    sys.modules["langchain"] = langchain
    sys.modules["langchain.agents"] = langchain_agents
    sys.modules["langchain.agents.middleware"] = langchain_agents_middleware
    sys.modules["langchain.agents.middleware.types"] = langchain_agents_middleware_types

from middleware.device_context_compression import DeviceContextCompressionMiddleware, compact_device_messages

for _name, _module in _ORIGINAL_LANGCHAIN_MODULES.items():
    if _module is None:
        sys.modules.pop(_name, None)
    else:
        sys.modules[_name] = _module


PRIOR_XML = """<hierarchy>
  <node text="" bounds="[0,0][1080,2400]">
    <node text="美食" bounds="[260,580][420,700]" />
    <node text="关闭" bounds="[900,100][1040,220]" />
  </node>
</hierarchy>"""

AFTER_TAP_XML = """<hierarchy><node text="商家列表" bounds="[0,0][1080,2400]" /></hierarchy>"""
LATEST_XML = """<hierarchy><node text="最新页面" bounds="[0,0][1080,2400]" /></hierarchy>"""


def assistant_tool_call(call_id: str, name: str, args: dict) -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": call_id, "name": name, "args": args}],
    }


def tool_message(call_id: str, name: str, content) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "name": name, "content": content}


class DeviceContextCompressionTests(unittest.TestCase):
    def test_keeps_only_latest_xml_result_raw(self) -> None:
        messages = [
            assistant_tool_call("call_1", "uiautomate", {}),
            tool_message("call_1", "uiautomate", PRIOR_XML),
            assistant_tool_call("call_2", "swipe_up", {"x": 200, "y": 1000}),
            tool_message("call_2", "swipe_up", AFTER_TAP_XML),
            assistant_tool_call("call_3", "uiautomate", {}),
            tool_message("call_3", "uiautomate", LATEST_XML),
        ]

        compacted = compact_device_messages(messages)

        self.assertEqual(compacted[1]["content"], "read current page XML")
        self.assertEqual(compacted[3]["content"], "swipe up on (200, 1000) to load more shops or content")
        self.assertEqual(compacted[5]["content"], LATEST_XML)

    def test_tap_summary_uses_target_from_prior_xml(self) -> None:
        messages = [
            assistant_tool_call("call_1", "uiautomate", {}),
            tool_message("call_1", "uiautomate", PRIOR_XML),
            assistant_tool_call("call_2", "tap", {"x": 320, "y": 620}),
            tool_message("call_2", "tap", AFTER_TAP_XML),
            assistant_tool_call("call_3", "uiautomate", {}),
            tool_message("call_3", "uiautomate", LATEST_XML),
        ]

        compacted = compact_device_messages(messages)

        self.assertEqual(compacted[3]["content"], "tap '美食' on (320, 620) to load a subpage")

    def test_tap_summary_falls_back_when_target_is_unknown(self) -> None:
        messages = [
            assistant_tool_call("call_1", "tap", {"x": 20, "y": 20}),
            tool_message("call_1", "tap", AFTER_TAP_XML),
            assistant_tool_call("call_2", "uiautomate", {}),
            tool_message("call_2", "uiautomate", LATEST_XML),
        ]

        compacted = compact_device_messages(messages)

        self.assertEqual(compacted[1]["content"], "tap on (20, 20) to interact with the visible target")

    def test_preserves_non_device_and_error_tool_results(self) -> None:
        error = {"ok": False, "error_type": "command_error", "message": "adb failed"}
        messages = [
            assistant_tool_call("call_1", "append_excel_rows", {"file_path": "stores.xlsx"}),
            tool_message("call_1", "append_excel_rows", "wrote 1 row"),
            assistant_tool_call("call_2", "tap", {"x": 100, "y": 100}),
            tool_message("call_2", "tap", error),
            assistant_tool_call("call_3", "uiautomate", {}),
            tool_message("call_3", "uiautomate", LATEST_XML),
        ]

        compacted = compact_device_messages(messages)

        self.assertEqual(compacted[1]["content"], "wrote 1 row")
        self.assertEqual(compacted[3]["content"], error)
        self.assertEqual(compacted[5]["content"], LATEST_XML)

    def test_middleware_before_model_returns_compacted_messages(self) -> None:
        middleware = DeviceContextCompressionMiddleware()
        messages = [
            assistant_tool_call("call_1", "uiautomate", {}),
            tool_message("call_1", "uiautomate", PRIOR_XML),
            assistant_tool_call("call_2", "uiautomate", {}),
            tool_message("call_2", "uiautomate", LATEST_XML),
        ]

        update = middleware.before_model({"messages": messages})

        self.assertIsNotNone(update)
        self.assertEqual(update["messages"][1]["content"], "read current page XML")
        self.assertEqual(update["messages"][3]["content"], LATEST_XML)


if __name__ == "__main__":
    unittest.main()
