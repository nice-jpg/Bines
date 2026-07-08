from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import COLLECTOR_PROMPT, SYSTEM_PROMPT


class SystemPromptTests(unittest.TestCase):
    def test_conductor_prompt_exposes_new_tool_contract(self) -> None:
        self.assertIn(
            "Device: run_package, close_package, uiautomate, screenshot, tap, swipe_up, swipe_down, swipe_back",
            SYSTEM_PROMPT,
        )
        self.assertIn("Page docs: query_page_doc, query_task_doc, set_current_page", SYSTEM_PROMPT)
        self.assertIn("Collector: prepare_collector", SYSTEM_PROMPT)
        self.assertNotIn("query_manual", SYSTEM_PROMPT)
        self.assertNotIn("Excel", SYSTEM_PROMPT)
        self.assertNotIn("spawn_subagent", SYSTEM_PROMPT)

    def test_conductor_prompt_requires_page_entry_order(self) -> None:
        self.assertIn("call set_current_page", SYSTEM_PROMPT)
        self.assertIn("Call query_page_doc", SYSTEM_PROMPT)
        self.assertIn("Call query_task_doc", SYSTEM_PROMPT)
        self.assertIn("Call prepare_collector before operating that page", SYSTEM_PROMPT)
        self.assertIn("Do not include task.md operation steps", SYSTEM_PROMPT)

    def test_conductor_prompt_forbids_direct_collection(self) -> None:
        self.assertIn("Do not collect merchant/product fields directly", SYSTEM_PROMPT)
        self.assertIn("Do not edit run.py", SYSTEM_PROMPT)
        self.assertIn("transparent automatic collection", SYSTEM_PROMPT)
        self.assertIn("must not extract information yourself", SYSTEM_PROMPT)

    def test_collector_prompt_has_isolated_fixed_loop(self) -> None:
        self.assertIn("Keep your own context", COLLECTOR_PROMPT)
        self.assertIn("collector_uiautomate", COLLECTOR_PROMPT)
        self.assertIn("collector_swipe_up", COLLECTOR_PROMPT)
        self.assertIn("Read and write only the current page's run.py", COLLECTOR_PROMPT)
        self.assertIn("Call save_baseline immediately after producing baseline r1", COLLECTOR_PROMPT)
        self.assertIn("Use sniff_run.evaluation as the only pass/fail signal", COLLECTOR_PROMPT)
        self.assertIn("sniff_run.evaluation.score is 100", COLLECTOR_PROMPT)
        self.assertIn("Do not decide pass/fail yourself", COLLECTOR_PROMPT)
        self.assertIn("sniff_run evaluation score is the only completion signal", COLLECTOR_PROMPT)
        self.assertIn("Do not evade a bad score by scrolling away", COLLECTOR_PROMPT)
        self.assertIn("general extraction heuristic for same-class pages", COLLECTOR_PROMPT)
        self.assertIn("Define run(xml: str) -> dict", COLLECTOR_PROMPT)
        self.assertNotIn("Call eval_collection", COLLECTOR_PROMPT)
        self.assertIn("Do not tap", COLLECTOR_PROMPT)
        self.assertIn("Do not enter a lower-level page", COLLECTOR_PROMPT)


if __name__ == "__main__":
    unittest.main()
