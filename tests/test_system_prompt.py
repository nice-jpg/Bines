from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import SYSTEM_PROMPT


class SystemPromptTests(unittest.TestCase):
    def test_prompt_requires_scrolling_list_pages_until_terminal_condition(self) -> None:
        self.assertIn("run_package: open the target app by Android package name", SYSTEM_PROMPT)
        self.assertIn("call swipe_up to load the next screen", SYSTEM_PROMPT)
        self.assertIn("2 consecutive swipe_up attempts add no new XML or merchant set", SYSTEM_PROMPT)
        self.assertIn("Do not return just because the first screen has few merchants", SYSTEM_PROMPT)
        self.assertIn("an out-of-range merchant is not a stop condition", SYSTEM_PROMPT)

    def test_prompt_requires_range_filtering_and_detail_scanning(self) -> None:
        self.assertIn("Do not start bulk collection until the range is confirmed to be active", SYSTEM_PROMPT)
        self.assertIn("1000 means 1000 meters", SYSTEM_PROMPT)
        self.assertIn("500m, 1.2km, and about 800 meters", SYSTEM_PROMPT)
        self.assertIn("Merchants without distance information must not be discarded immediately", SYSTEM_PROMPT)
        self.assertIn("Run a scrolling scan over the product list", SYSTEM_PROMPT)
        self.assertIn("total review count and positive review count", SYSTEM_PROMPT)
        self.assertIn("immediately organize the collected data by merchant and append it to the Excel file", SYSTEM_PROMPT)

    def test_prompt_describes_page_specific_operation_strategy(self) -> None:
        self.assertIn("Prefer the run_package tool", SYSTEM_PROMPT)
        self.assertIn("Do not use y=0 or y=2400", SYSTEM_PROMPT)
        self.assertIn("First-level page logic: directly find and tap the secondary page entry", SYSTEM_PROMPT)
        self.assertIn("top to bottom into a search box, a service icon grid, and a merchant list", SYSTEM_PROMPT)
        self.assertIn("prefer swiping within the merchant list area", SYSTEM_PROMPT)
        self.assertIn("tap the merchant icon or title to enter the merchant", SYSTEM_PROMPT)
        self.assertIn("basic merchant information in the upper area", SYSTEM_PROMPT)
        self.assertIn("left side is a separately scrollable category list", SYSTEM_PROMPT)
        self.assertIn("right side is the product list", SYSTEM_PROMPT)
        self.assertIn("use swipe_back to return to the previous page", SYSTEM_PROMPT)
        self.assertIn("you are unsure what to do next", SYSTEM_PROMPT)

    def test_prompt_requires_human_like_safe_clicking_and_unexpected_page_recovery(self) -> None:
        self.assertIn("Before tapping, judge the target element's position and visibility", SYSTEM_PROMPT)
        self.assertIn("first use swipe_up/swipe_down to move it fully into view", SYSTEM_PROMPT)
        self.assertIn("Merchant cards are complex", SYSTEM_PROMPT)
        self.assertIn("prefer tapping the merchant icon, merchant avatar, or merchant title", SYSTEM_PROMPT)
        self.assertIn("Do not tap coupon, delivery, campaign, product preview, favorite, or review areas", SYSTEM_PROMPT)
        self.assertIn("tap the merchant icon or title to enter the merchant", SYSTEM_PROMPT)
        self.assertIn("not the target merchant detail page", SYSTEM_PROMPT)
        self.assertIn("immediately use swipe_back to return to the previous level", SYSTEM_PROMPT)

    def test_prompt_requires_aggressive_collection_until_no_new_information(self) -> None:
        self.assertIn("you must scroll to the bottom", SYSTEM_PROMPT)
        self.assertIn("Do not stop early just because a lot of data has already been collected", SYSTEM_PROMPT)
        self.assertIn("Continue collecting new data unless 3 consecutive swipe_up attempts add no new information", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
