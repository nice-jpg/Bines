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


class ToolsRegistryTests(unittest.TestCase):
    def test_collect_tools_includes_thinking_tool(self) -> None:
        tool_names = [tool.name for tool in collect_tools()]

        self.assertIn("think", tool_names)
        self.assertLess(tool_names.index("think"), tool_names.index("tap"))


if __name__ == "__main__":
    unittest.main()
