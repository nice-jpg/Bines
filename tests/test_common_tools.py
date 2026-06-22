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

from tools.common import ManualTool, OperationNoticeTool, ThinkingTool, create_common_tools


class FakeLogger:
    def __init__(self) -> None:
        self.records = []

    def log(self, category, message, details=None) -> None:
        self.records.append((category, message, details or {}))


class CommonToolTests(unittest.TestCase):
    def test_notify_user_returns_operation_log_and_persists_notice(self) -> None:
        logger = FakeLogger()
        result = OperationNoticeTool(logger=logger).notify_user(
            operation="swipe up on (200, 1200) to load more shops",
            current_path="meituan/外卖",
            reason="current list screen has no unprocessed visible shops",
        )

        self.assertIn("<operation_log>", result)
        self.assertIn("swipe up on (200, 1200) to load more shops", result)
        self.assertIn('current_path="meituan/外卖"', result)
        self.assertIn("current list screen has no unprocessed visible shops", result)
        self.assertEqual(logger.records[0][0], "operation_notice")
        self.assertEqual(logger.records[0][1], "swipe up on (200, 1200) to load more shops")
        self.assertEqual(logger.records[0][2]["current_path"], "meituan/外卖")

    def test_notify_user_calls_optional_notifier_without_changing_result(self) -> None:
        delivered = []
        result = OperationNoticeTool(
            logger=FakeLogger(),
            notifier=delivered.append,
        ).notify_user(
            operation="tap '美食' on (320, 620) to load a subpage",
            current_path="meituan",
            reason="open configured secondary page",
            next_page="美食",
            next_path="meituan/美食",
        )

        self.assertIn("<operation_log>", result)
        self.assertEqual(len(delivered), 1)
        self.assertIn("Operation: tap '美食' on (320, 620) to load a subpage", delivered[0])
        self.assertIn("Next path: meituan/美食", delivered[0])

    def test_notify_user_records_expected_next_page_context(self) -> None:
        result = OperationNoticeTool(logger=FakeLogger()).notify_user(
            operation="tap '美食' on (320, 620) to load a subpage",
            current_path="meituan",
            reason="open configured secondary page",
            next_page="美食",
            next_path="meituan/美食",
        )

        self.assertIn('next_page="美食"', result)
        self.assertIn('next_path="meituan/美食"', result)

    def test_notify_user_keeps_recent_log_tail(self) -> None:
        tool = OperationNoticeTool(logger=FakeLogger(), max_entries=2)

        tool.notify_user("first operation", "meituan")
        tool.notify_user("second operation", "meituan")
        result = tool.notify_user("third operation", "meituan")

        self.assertNotIn("first operation", result)
        self.assertIn("second operation", result)
        self.assertIn("third operation", result)
        self.assertIn('count="2" max="2"', result)

    def test_notify_user_handles_empty_operation_without_raising(self) -> None:
        self.assertEqual(
            OperationNoticeTool(logger=FakeLogger()).notify_user("  ", "meituan"),
            "No operation notice was recorded because the operation input was empty.",
        )

    def test_think_records_non_empty_thought_without_side_effect_output(self) -> None:
        self.assertEqual(ThinkingTool().think("XML shows three shops; next scroll."), "Thought recorded.")

    def test_think_handles_empty_input(self) -> None:
        self.assertEqual(ThinkingTool().think("  "), "No thought was recorded because the input was empty.")

    def test_create_common_tools_registers_think_tool(self) -> None:
        tools = create_common_tools()

        self.assertEqual([tool.name for tool in tools], ["notify_user", "think", "query_manual"])
        self.assertIn("before any external device or Excel operation", tools[0].description)
        self.assertIn("complex tool outputs", tools[1].description)
        self.assertIn("does not fetch new information", tools[1].description)

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

        self.assertIn('canonical_path="meituan/外卖/商家"', result)
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

        self.assertIn('canonical_path="meituan"', result)
        self.assertIn('path="meituan/PAGE.md"', result)
        self.assertIn("Home manual", result)

    def test_query_manual_reports_missing_docs_without_raising(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = ManualTool(tmp_dir).query_manual("meituan/酒店")

        self.assertIn("<manual_error>", result)
        self.assertIn("<attempted_path>meituan/酒店</attempted_path>", result)
        self.assertIn("No manual found for the canonical path.", result)

    def test_query_manual_does_not_map_home_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for relative_path, content in {
                "meituan/PAGE.md": "Home manual",
                "meituan/外卖/PAGE.md": "Waimai manual",
                "meituan/美食/PAGE.md": "Food manual",
            }.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            result = ManualTool(root).query_manual("美团/首页")

        self.assertIn("<manual_error>", result)
        self.assertIn("<input_path>美团/首页</input_path>", result)
        self.assertIn("<attempted_path>meituan/首页</attempted_path>", result)
        self.assertIn("<path>meituan</path>", result)
        self.assertIn("<path>meituan/美食</path>", result)

    def test_query_manual_does_not_map_concrete_merchant_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            for relative_path, content in {
                "meituan/美食/PAGE.md": "Food manual",
                "meituan/美食/商家/PAGE.md": "Food merchant manual",
            }.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            result = ManualTool(root).query_manual("美团/美食/老乡鸡")

        self.assertIn("<manual_error>", result)
        self.assertIn("<attempted_path>meituan/美食/老乡鸡</attempted_path>", result)
        self.assertIn("<available_child_paths>", result)
        self.assertIn("<path>meituan/美食/商家</path>", result)

    def test_query_manual_returns_error_for_path_escape_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            path = root / "meituan" / "PAGE.md"
            path.parent.mkdir(parents=True)
            path.write_text("Home manual", encoding="utf-8")

            parent_result = ManualTool(root).query_manual("../meituan")
            folded_parent_result = ManualTool(root).query_manual("meituan/../secret")
            absolute_result = ManualTool(root).query_manual("/meituan")

        self.assertIn("<manual_error>", parent_result)
        self.assertIn("must not contain '..'", parent_result)
        self.assertIn("<manual_error>", folded_parent_result)
        self.assertIn("must not contain '..'", folded_parent_result)
        self.assertIn("<manual_error>", absolute_result)
        self.assertIn("must be relative", absolute_result)


if __name__ == "__main__":
    unittest.main()
