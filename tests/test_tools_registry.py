from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

if "langchain_core.tools" not in sys.modules:
    langchain_core = types.ModuleType("langchain_core")
    langchain_core_tools = types.ModuleType("langchain_core.tools")

    class StructuredTool:
        @classmethod
        def from_function(cls, **kwargs):
            instance = cls()
            instance.name = kwargs["name"]
            instance.description = kwargs["description"]
            instance.func = kwargs["func"]
            return instance

    langchain_core_tools.StructuredTool = StructuredTool
    sys.modules["langchain_core"] = langchain_core
    sys.modules["langchain_core.tools"] = langchain_core_tools

from tools import collect_tools


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
        self.assertIn("notify_user", tool_names)
        self.assertIn("authenticate_captcha", tool_names)
        self.assertIn("think", tool_names)
        self.assertIn("query_manual", tool_names)
        self.assertLess(tool_names.index("notify_user"), tool_names.index("tap"))
        self.assertLess(tool_names.index("authenticate_captcha"), tool_names.index("tap"))
        self.assertLess(tool_names.index("think"), tool_names.index("tap"))
        self.assertLess(tool_names.index("query_manual"), tool_names.index("tap"))

    def test_collect_tools_can_include_subagent_tools_for_main_agent(self) -> None:
        tool_names = [
            tool.name
            for tool in collect_tools(include_subagents=True, subagent_manager=FakeSubagentManager())
        ]

        self.assertIn("spawn_subagent", tool_names)
        self.assertIn("call_subagent", tool_names)
        self.assertIn("kill_subagent", tool_names)
        self.assertGreater(tool_names.index("spawn_subagent"), tool_names.index("append_excel_rows"))


if __name__ == "__main__":
    unittest.main()
