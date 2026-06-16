from __future__ import annotations

import sys
import tempfile
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

from tools.common import ManualTool, ThinkingTool, create_common_tools


class CommonToolTests(unittest.TestCase):
    def test_think_records_non_empty_thought_without_side_effect_output(self) -> None:
        self.assertEqual(ThinkingTool().think("XML shows three shops; next scroll."), "Thought recorded.")

    def test_think_handles_empty_input(self) -> None:
        self.assertEqual(ThinkingTool().think("  "), "No thought was recorded because the input was empty.")

    def test_create_common_tools_registers_think_tool(self) -> None:
        tools = create_common_tools()

        self.assertEqual([tool.name for tool in tools], ["think", "query_manual"])
        self.assertIn("complex tool outputs", tools[0].description)
        self.assertIn("does not fetch new information", tools[0].description)

    def test_query_manual_returns_only_current_page_doc_for_operation_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for relative_path, content in {
                "meituan/PAGE.md": "Home manual",
                "meituan/外卖/PAGE.md": "Waimai manual",
                "meituan/外卖/商家/PAGE.md": "Merchant manual",
                "meituan/美食/PAGE.md": "Food manual",
            }.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            result = ManualTool(root).query_manual("com.sankuai.meituan/外卖/商家")

        self.assertNotIn('path="meituan/PAGE.md"', result)
        self.assertNotIn("Home manual", result)
        self.assertNotIn('path="meituan/外卖/PAGE.md"', result)
        self.assertNotIn("Waimai manual", result)
        self.assertIn('path="meituan/外卖/商家/PAGE.md"', result)
        self.assertIn("Merchant manual", result)
        self.assertNotIn("Food manual", result)

    def test_query_manual_returns_only_app_doc_for_app_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "meituan" / "PAGE.md"
            path.parent.mkdir(parents=True)
            path.write_text("Home manual", encoding="utf-8")

            result = ManualTool(root).query_manual("美团")

        self.assertIn('path="meituan/PAGE.md"', result)
        self.assertIn("Home manual", result)

    def test_query_manual_reports_missing_docs_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = ManualTool(tmp_dir).query_manual("meituan/酒店")

        self.assertEqual(result, "No manual found for path: meituan/酒店")


if __name__ == "__main__":
    unittest.main()
