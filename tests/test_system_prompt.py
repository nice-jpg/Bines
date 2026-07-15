from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import COLLECTOR_PROMPT, SYSTEM_PROMPT


class SystemPromptTests(unittest.TestCase):
    def test_conductor_prompt_exposes_new_tool_contract(self) -> None:
        self.assertIn(
            "Device: run_package, close_package, uiautomate, screenshot, tap_in_page, navigate_to, navigate_back, swipe_up, swipe_down",
            SYSTEM_PROMPT,
        )
        self.assertNotIn("Page docs:", SYSTEM_PROMPT)
        self.assertIn("Collector: prepare_collector", SYSTEM_PROMPT)
        self.assertNotIn("query_manual", SYSTEM_PROMPT)
        self.assertNotIn("Excel", SYSTEM_PROMPT)
        self.assertNotIn("spawn_subagent", SYSTEM_PROMPT)

    def test_conductor_prompt_requires_explicit_navigation_and_runtime_manuals(self) -> None:
        self.assertIn("runtime sets the app home path and injects", SYSTEM_PROMPT)
        self.assertIn("Use tap_in_page only", SYSTEM_PROMPT)
        self.assertIn("Use navigate_to", SYSTEM_PROMPT)
        self.assertIn("Use navigate_back", SYSTEM_PROMPT)
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
        self.assertIn("sniff_run.evaluation as the only per-state pass/fail signal", COLLECTOR_PROMPT)
        self.assertIn("sniff_run.verification.ready is true", COLLECTOR_PROMPT)
        self.assertIn("Do not decide pass/fail yourself", COLLECTOR_PROMPT)
        self.assertIn("only training-completion signal", COLLECTOR_PROMPT)
        self.assertIn("same run.py fingerprint passed at least two different swiped XML states", COLLECTOR_PROMPT)
        self.assertIn("Do not evade a bad score by scrolling away", COLLECTOR_PROMPT)
        self.assertIn("general extraction heuristic for same-class pages", COLLECTOR_PROMPT)
        self.assertIn("Define run(xml: str) -> dict", COLLECTOR_PROMPT)
        self.assertNotIn("Call eval_collection", COLLECTOR_PROMPT)
        self.assertIn("Do not tap", COLLECTOR_PROMPT)
        self.assertIn("Do not enter a lower-level page", COLLECTOR_PROMPT)


if __name__ == "__main__":
    unittest.main()
