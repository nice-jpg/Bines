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

from tools.common import ThinkingTool, create_common_tools


class CommonToolTests(unittest.TestCase):
    def test_think_records_non_empty_thought_without_side_effect_output(self) -> None:
        self.assertEqual(ThinkingTool().think("XML shows three shops; next scroll."), "Thought recorded.")

    def test_think_handles_empty_input(self) -> None:
        self.assertEqual(ThinkingTool().think("  "), "No thought was recorded because the input was empty.")

    def test_create_common_tools_registers_think_tool(self) -> None:
        tools = create_common_tools()

        self.assertEqual([tool.name for tool in tools], ["think"])
        self.assertIn("complex tool outputs", tools[0].description)
        self.assertIn("does not fetch new information", tools[0].description)


if __name__ == "__main__":
    unittest.main()
