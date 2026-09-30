from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tools import collect_conductor_tools, collect_tools
from tools.operation_effect import (
    NAVIGATION_PAGE_CHANGE_POLICY,
    OPERATION_EFFECT_POLICY_METADATA_KEY,
    SCROLL_PAGE_CHANGE_POLICY,
)
from tools.operation_policy import EXTERNAL_OPERATION_POLICY_METADATA_KEY


class FakeSubagentManager:
    def spawn_subagent(self, name, agent_type, instructions, tool_names="", max_iterations=80):
        return "spawned"

    def call_subagent(self, agent_id, task):
        return "called"

    def kill_subagent(self, agent_id):
        return "killed"


class ToolsRegistryTests(unittest.TestCase):
    def test_collect_tools_includes_thinking_tool(self) -> None:
        tool_names = [tool.name for tool in collect_tools()]

        self.assertNotIn("spawn_subagent", tool_names)
        self.assertNotIn("call_subagent", tool_names)
        self.assertNotIn("kill_subagent", tool_names)
        self.assertNotIn("query_manual", tool_names)
        self.assertIn("notify_user", tool_names)
        self.assertNotIn("start_shadow_service", tool_names)
        self.assertNotIn("stop_shadow_service", tool_names)
        self.assertIn("authenticate_captcha", tool_names)
        self.assertIn("captcha_authenticated", tool_names)
        self.assertIn("think", tool_names)
        self.assertNotIn("set_current_page", tool_names)
        self.assertNotIn("query_page_doc", tool_names)
        self.assertNotIn("query_task_doc", tool_names)
        self.assertNotIn("tap", tool_names)
        self.assertNotIn("swipe_back", tool_names)
        self.assertIn("tap_in_page", tool_names)
        self.assertIn("navigate_to", tool_names)
        self.assertIn("navigate_back", tool_names)
        self.assertIn("close_package", tool_names)
        self.assertIn("swipe_up_long", tool_names)
        self.assertNotIn("swipe_to_random", tool_names)
        self.assertIn("prepare_collector", tool_names)
        self.assertNotIn("create_excel_sheet", tool_names)
        self.assertNotIn("create_excel_file", tool_names)
        self.assertNotIn("rename_excel_sheet", tool_names)
        self.assertNotIn("write_excel_sheet", tool_names)
        self.assertNotIn("append_excel_rows", tool_names)
        self.assertNotIn("update_excel_cell", tool_names)
        self.assertLess(tool_names.index("notify_user"), tool_names.index("tap_in_page"))
        self.assertLess(tool_names.index("authenticate_captcha"), tool_names.index("tap_in_page"))
        self.assertLess(tool_names.index("captcha_authenticated"), tool_names.index("tap_in_page"))
        self.assertLess(tool_names.index("think"), tool_names.index("tap_in_page"))

        schemas = {
            tool.name: tool.tool_call_schema if isinstance(tool.tool_call_schema, dict)
            else tool.tool_call_schema.model_json_schema()
            for tool in collect_conductor_tools()
        }
        for tool_name in ("run_package", "close_package"):
            schema = schemas[tool_name]
            self.assertEqual(set(schema["properties"]), {"package_name", "justification"})
            self.assertEqual(set(schema["required"]), {"package_name", "justification"})
            justification = schema["$defs"]["DangerousActionJustification"]
            self.assertEqual(
                set(justification["required"]),
                {"reason", "evidence", "expected_outcome", "risk_acknowledged"},
            )
            self.assertTrue(justification["properties"]["risk_acknowledged"]["const"])
        self.assertEqual(set(schemas["navigate_to"]["properties"]), {"x", "y", "next_path", "merchant_name"})
        self.assertEqual(schemas["navigate_to"]["properties"]["merchant_name"]["type"], "string")
        self.assertNotIn("merchant_name", schemas["navigate_to"]["required"])
        self.assertEqual(set(schemas["navigate_back"]["properties"]), {"next_path"})
        self.assertNotIn("runtime", schemas["navigate_to"]["properties"])
        self.assertEqual(
            set(schemas["prepare_collector"]["properties"]),
            {"page_path", "page_description", "merchant_schema", "collection_schema"},
        )
        self.assertNotIn("runtime", schemas["prepare_collector"]["properties"])

    def test_collect_tools_can_include_subagent_tools_for_main_agent(self) -> None:
        tool_names = [
            tool.name
            for tool in collect_tools(include_subagents=True, subagent_manager=FakeSubagentManager())
        ]

        self.assertIn("spawn_subagent", tool_names)
        self.assertIn("call_subagent", tool_names)
        self.assertIn("kill_subagent", tool_names)
        self.assertIn("query_manual", tool_names)
        self.assertNotIn("swipe_to_random", tool_names)
        self.assertGreater(tool_names.index("spawn_subagent"), tool_names.index("append_excel_rows"))

    def test_operation_effect_policies_are_typed_and_narrowly_exposed(self) -> None:
        tools = {tool.name: tool for tool in collect_conductor_tools()}

        self.assertIs(
            tools["navigate_to"].metadata[OPERATION_EFFECT_POLICY_METADATA_KEY],
            NAVIGATION_PAGE_CHANGE_POLICY,
        )
        for name in ("swipe_up", "swipe_up_long", "swipe_down"):
            self.assertIs(
                tools[name].metadata[OPERATION_EFFECT_POLICY_METADATA_KEY],
                SCROLL_PAGE_CHANGE_POLICY,
            )
        for name in ("tap_in_page", "navigate_back", "run_package", "close_package", "page_view"):
            self.assertNotIn(OPERATION_EFFECT_POLICY_METADATA_KEY, tools[name].metadata or {})

    def test_notifications_are_limited_to_probe_lifecycle_milestones(self) -> None:
        tools = {tool.name: tool for tool in collect_conductor_tools()}

        for name in ("run_package", "prepare_collector", "close_package"):
            self.assertIn(EXTERNAL_OPERATION_POLICY_METADATA_KEY, tools[name].metadata or {})
        for name in (
            "tap_in_page",
            "navigate_to",
            "navigate_back",
            "swipe_up",
            "swipe_up_long",
            "swipe_down",
            "page_view",
            "screenshot",
        ):
            self.assertNotIn(EXTERNAL_OPERATION_POLICY_METADATA_KEY, tools[name].metadata or {})

if __name__ == "__main__":
    unittest.main()
