from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import SYSTEM_PROMPT


class SystemPromptTests(unittest.TestCase):
    def test_prompt_keeps_generic_tool_and_scanning_rules(self) -> None:
        self.assertIn("run_package: open the target app by Android package name", SYSTEM_PROMPT)
        self.assertIn("notify_user: tell the user what external operation", SYSTEM_PROMPT)
        self.assertIn("think: reflect on complex tool outputs", SYSTEM_PROMPT)
        self.assertIn("query_manual: read the PAGE.md manual", SYSTEM_PROMPT)
        self.assertIn("spawn_subagent: create an independent or delegated synchronous subagent", SYSTEM_PROMPT)
        self.assertIn("call_subagent: call an existing subagent and wait", SYSTEM_PROMPT)
        self.assertIn("kill_subagent: remove a subagent", SYSTEM_PROMPT)
        self.assertIn("Prefer uiautomate for XML analysis", SYSTEM_PROMPT)
        self.assertIn("Use screenshot when the XML lacks useful information", SYSTEM_PROMPT)
        self.assertIn("Do not use y=0 or y=2400", SYSTEM_PROMPT)
        self.assertIn("you must scroll to the bottom", SYSTEM_PROMPT)
        self.assertIn("Continue collecting new data unless 3 consecutive swipe_up attempts add no new information", SYSTEM_PROMPT)

    def test_prompt_uses_query_manual_for_app_specific_logic(self) -> None:
        self.assertIn("Use query_manual with the current app or operation path", SYSTEM_PROMPT)
        self.assertIn("It returns only the current page manual", SYSTEM_PROMPT)
        self.assertIn("Page-specific operating logic is not preloaded", SYSTEM_PROMPT)
        self.assertIn("Follow the returned manual context", SYSTEM_PROMPT)
        self.assertIn("valid targets, safe clickable areas, and expected next page", SYSTEM_PROMPT)
        self.assertNotIn("<page_mechanism_context>", SYSTEM_PROMPT)

    def test_prompt_requires_canonical_manual_paths(self) -> None:
        self.assertIn("Manual paths are typed operation paths", SYSTEM_PROMPT)
        self.assertIn("do not query `美团/首页`", SYSTEM_PROMPT)
        self.assertIn("Whenever the page level changes, call query_manual", SYSTEM_PROMPT)
        self.assertIn("use the generic merchant manual path", SYSTEM_PROMPT)
        self.assertIn("Do not use a specific merchant name", SYSTEM_PROMPT)
        self.assertIn("If query_manual returns manual_error", SYSTEM_PROMPT)
        self.assertIn("Keep notify_user.next_path and query_manual.current_path aligned", SYSTEM_PROMPT)

    def test_prompt_keeps_generic_range_and_output_requirements(self) -> None:
        self.assertIn("1000 means 1000 meters", SYSTEM_PROMPT)
        self.assertIn("500m, 1.2km, and about 800 meters", SYSTEM_PROMPT)
        self.assertIn("Do not start bulk collection until the required filters and range are confirmed", SYSTEM_PROMPT)
        self.assertIn("Items without distance information must not be discarded immediately", SYSTEM_PROMPT)
        self.assertIn("Store collected information in an Excel file grouped by merchant", SYSTEM_PROMPT)
        self.assertIn("Write each merchant to Excel immediately", SYSTEM_PROMPT)

    def test_prompt_requires_human_like_safe_clicking_and_unexpected_page_recovery(self) -> None:
        self.assertIn("Before tapping, judge the target element's position and visibility", SYSTEM_PROMPT)
        self.assertIn("first use swipe_up/swipe_down to move it fully into view", SYSTEM_PROMPT)
        self.assertIn("Every operation must serve a clear goal", SYSTEM_PROMPT)
        self.assertIn("If the page is clearly not the expected detail page", SYSTEM_PROMPT)
        self.assertIn("immediately use swipe_back to return to the previous level", SYSTEM_PROMPT)

    def test_prompt_requires_operation_notice_before_external_actions(self) -> None:
        self.assertIn("Before every device tool call, call notify_user", SYSTEM_PROMPT)
        self.assertIn("Before every Excel output tool call, call notify_user", SYSTEM_PROMPT)
        self.assertIn("Before spawning, calling, or killing a subagent, call notify_user", SYSTEM_PROMPT)
        self.assertIn("notify_user must include next_page and next_path", SYSTEM_PROMPT)
        self.assertIn("Use the returned operation_log as live context", SYSTEM_PROMPT)
        self.assertIn("You do not need to call notify_user before think, query_manual, or notify_user itself", SYSTEM_PROMPT)
        self.assertIn("that goal must be readable in notify_user", SYSTEM_PROMPT)

    def test_prompt_guides_subagent_delegation(self) -> None:
        self.assertIn("Independent subagents solve standalone analysis tasks", SYSTEM_PROMPT)
        self.assertIn("Delegated subagents receive a copy of your current runtime context", SYSTEM_PROMPT)
        self.assertIn("Subagents cannot create or call other subagents", SYSTEM_PROMPT)
        self.assertIn("All agents that operate the device must run serially", SYSTEM_PROMPT)
        self.assertIn("prefer delegating each single merchant's information collection", SYSTEM_PROMPT)
        self.assertIn("wait for the returned subagent_result before doing any further device operation", SYSTEM_PROMPT)
        self.assertIn("keep instructions and task text focused on the task goal", SYSTEM_PROMPT)
        self.assertIn("Do not pass merchant introductions, product summaries, copied page text", SYSTEM_PROMPT)
        self.assertIn("subagent can read itself from query_manual, uiautomate, or screenshot", SYSTEM_PROMPT)
        self.assertIn("The subagent must use query_manual", SYSTEM_PROMPT)
        self.assertIn("receive a final fork directive", SYSTEM_PROMPT)

    def test_prompt_does_not_include_page_specific_meituan_details(self) -> None:
        self.assertNotIn("service icon grid", SYSTEM_PROMPT)
        self.assertNotIn("Merchant cards are complex", SYSTEM_PROMPT)
        self.assertNotIn("left side is a separately scrollable category list", SYSTEM_PROMPT)
        self.assertNotIn("right side is the product list", SYSTEM_PROMPT)


if __name__ == "__main__":
    unittest.main()
