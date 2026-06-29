from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import SYSTEM_PROMPT


class SystemPromptTests(unittest.TestCase):
    def test_prompt_keeps_generic_tool_and_scanning_rules(self) -> None:
        self.assertIn(
            "Device: run_package, close_package, uiautomate, screenshot, tap, swipe_up, swipe_down, swipe_back",
            SYSTEM_PROMPT,
        )
        self.assertIn("Context: notify_user, think, query_manual", SYSTEM_PROMPT)
        self.assertNotIn("start_shadow_service:", SYSTEM_PROMPT)
        self.assertNotIn("stop_shadow_service:", SYSTEM_PROMPT)
        self.assertIn("Prefer uiautomate for page analysis", SYSTEM_PROMPT)
        self.assertIn("use screenshot when XML is incomplete", SYSTEM_PROMPT)
        self.assertIn("never start at y=0 or y=2400", SYSTEM_PROMPT)
        self.assertIn("process all visible candidates, then swipe to load more", SYSTEM_PROMPT)

    def test_prompt_uses_query_manual_for_app_specific_logic(self) -> None:
        self.assertIn("Use query_manual before page-specific decisions", SYSTEM_PROMPT)
        self.assertIn("after every page-level change", SYSTEM_PROMPT)
        self.assertNotIn("<page_mechanism_context>", SYSTEM_PROMPT)

    def test_prompt_requires_canonical_manual_paths(self) -> None:
        self.assertIn("Manual paths are canonical operation paths", SYSTEM_PROMPT)
        self.assertIn("not UI titles or merchant names", SYSTEM_PROMPT)
        self.assertIn("merchant detail pages use `meituan/美食/商家` or `meituan/外卖/商家`", SYSTEM_PROMPT)
        self.assertIn("If query_manual returns manual_error", SYSTEM_PROMPT)

    def test_prompt_keeps_generic_range_and_output_requirements(self) -> None:
        self.assertIn("Treat numeric range as meters", SYSTEM_PROMPT)
        self.assertIn("500m, 1.2km, and about 800 meters", SYSTEM_PROMPT)
        self.assertIn("Scan each configured secondary page after city, address, and range are active", SYSTEM_PROMPT)
        self.assertIn("The only output workbook is `result.xlsx`", SYSTEM_PROMPT)
        self.assertIn("Create one worksheet per merchant", SYSTEM_PROMPT)
        self.assertIn("Name the worksheet with the merchant's complete name", SYSTEM_PROMPT)
        self.assertIn("every product from that merchant in this worksheet", SYSTEM_PROMPT)

    def test_prompt_requires_human_like_safe_clicking_and_unexpected_page_recovery(self) -> None:
        self.assertIn("Tap only visible, unobstructed targets", SYSTEM_PROMPT)
        self.assertIn("scroll them into view first", SYSTEM_PROMPT)

    def test_prompt_requires_operation_notice_before_external_actions(self) -> None:
        self.assertIn("Before every device, Excel, or subagent action, call notify_user", SYSTEM_PROMPT)
        self.assertIn("set next_page and next_path", SYSTEM_PROMPT)

    def test_prompt_requires_captcha_authentication_tool(self) -> None:
        self.assertIn("If captcha or human verification appears", SYSTEM_PROMPT)
        self.assertIn("call notify_user, then authenticate_captcha", SYSTEM_PROMPT)
        self.assertIn("call captcha_authenticated", SYSTEM_PROMPT)

    def test_prompt_guides_subagent_delegation(self) -> None:
        self.assertIn("Device operations must be serial", SYSTEM_PROMPT)
        self.assertIn("If using a delegated subagent for one merchant", SYSTEM_PROMPT)
        self.assertIn("wait for call_subagent to return", SYSTEM_PROMPT)

    def test_prompt_requires_complete_merchant_collection_before_exit(self) -> None:
        self.assertIn("For each merchant, collect: merchant name, rating, sales, distance", SYSTEM_PROMPT)
        self.assertIn("total review count, positive review count", SYSTEM_PROMPT)
        self.assertIn("every available product with product name, price, and sales", SYSTEM_PROMPT)
        self.assertIn("Do not leave a merchant page until all required merchant fields", SYSTEM_PROMPT)
        self.assertIn("Write that merchant to its Excel worksheet before returning to the list", SYSTEM_PROMPT)

    def test_prompt_requires_closing_the_app_after_collection(self) -> None:
        self.assertIn("After all configured collection work is complete", SYSTEM_PROMPT)
        self.assertIn("call notify_user and then close_package", SYSTEM_PROMPT)
        self.assertIn("configured application package name", SYSTEM_PROMPT)
        self.assertIn("Only return the final response after close_package succeeds", SYSTEM_PROMPT)

    def test_prompt_does_not_include_page_specific_meituan_details(self) -> None:
        self.assertNotIn("service icon grid", SYSTEM_PROMPT)
        self.assertNotIn("Merchant cards are complex", SYSTEM_PROMPT)
        self.assertNotIn("left side is a separately scrollable category list", SYSTEM_PROMPT)
        self.assertNotIn("right side is the product list", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
