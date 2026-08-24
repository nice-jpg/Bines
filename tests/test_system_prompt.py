from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import (
    COLLECTOR_CODER_PROMPT,
    COLLECTOR_PROMPT,
    RUN_AGENT_PY_PATCH_GRAMMAR,
    SYSTEM_PROMPT,
)


class SystemPromptTests(unittest.TestCase):
    def test_conductor_prompt_exposes_new_tool_contract(self) -> None:
        self.assertIn(
            "Device: run_package, close_package, uiautomate, screenshot, tap_in_page, navigate_to, navigate_back, swipe_up, swipe_up_long, swipe_down",
            SYSTEM_PROMPT,
        )
        self.assertNotIn("Page docs:", SYSTEM_PROMPT)
        self.assertIn("Collector: prepare_collector", SYSTEM_PROMPT)
        self.assertNotIn("query_manual", SYSTEM_PROMPT)
        self.assertNotIn("Excel", SYSTEM_PROMPT)
        self.assertNotIn("spawn_subagent", SYSTEM_PROMPT)

    def test_conductor_prompt_requires_explicit_navigation_and_runtime_manuals(self) -> None:
        self.assertIn("runtime sets the app home path and appends", SYSTEM_PROMPT)
        self.assertIn("latest `<current_page_manual>` system message is the authoritative manual", SYSTEM_PROMPT)
        self.assertIn("Use tap_in_page only", SYSTEM_PROMPT)
        self.assertIn("Use navigate_to", SYSTEM_PROMPT)
        self.assertIn("Use navigate_back", SYSTEM_PROMPT)
        self.assertIn("Call prepare_collector before operating that page", SYSTEM_PROMPT)
        self.assertIn("Do not include task.md operation steps", SYSTEM_PROMPT)

    def test_conductor_prompt_forbids_direct_collection(self) -> None:
        self.assertIn("Do not collect merchant/product fields directly", SYSTEM_PROMPT)
        self.assertIn("Do not edit run.agent.py", SYSTEM_PROMPT)
        self.assertIn("Do not call is_done after ordinary merchant product-list swipes", SYSTEM_PROMPT)
        self.assertIn("wait for new possible-end evidence before checking again", SYSTEM_PROMPT)
        self.assertIn("Only done=true authorizes navigate_back", SYSTEM_PROMPT)
        self.assertIn("transparent automatic collection", SYSTEM_PROMPT)
        self.assertIn("must not extract information yourself", SYSTEM_PROMPT)

    def test_conductor_prompt_requires_dangerous_tool_justification(self) -> None:
        self.assertIn("run_package and close_package are dangerous operations", SYSTEM_PROMPT)
        self.assertIn("risk_acknowledged=true", SYSTEM_PROMPT)
        self.assertIn("page tasks, collection, and archival are complete", SYSTEM_PROMPT)
        self.assertIn("Never use an operation-only or templated placeholder", SYSTEM_PROMPT)

    def test_collector_prompt_has_isolated_fixed_loop(self) -> None:
        self.assertIn("keeping isolated context", COLLECTOR_PROMPT)
        self.assertIn("collector_uiautomate", COLLECTOR_PROMPT)
        self.assertIn("collector_swipe_up", COLLECTOR_PROMPT)
        self.assertIn("collector_swipe_up_long", COLLECTOR_PROMPT)
        self.assertIn("collector_swipe_to_random", COLLECTOR_PROMPT)
        self.assertIn("consult_coder", COLLECTOR_PROMPT)
        self.assertIn("multiple materially different (XML, r1) training pairs", COLLECTOR_PROMPT)
        self.assertIn("every saved pair from the current and prior page visits", COLLECTOR_PROMPT)
        self.assertIn("same persistent coder agent", COLLECTOR_PROMPT)
        self.assertIn("test the existing run.agent.py", COLLECTOR_PROMPT)
        self.assertIn("If score=100, do not call consult_coder", COLLECTOR_PROMPT)
        self.assertIn("consult the same coder only when that batch test fails", COLLECTOR_PROMPT)
        self.assertNotIn("write_run_agent_py", COLLECTOR_PROMPT)
        self.assertNotIn("Patch grammar:", COLLECTOR_PROMPT)
        self.assertNotIn(RUN_AGENT_PY_PATCH_GRAMMAR, COLLECTOR_PROMPT)
        self.assertIn("immediately call save_baseline", COLLECTOR_PROMPT)
        self.assertIn("batch evaluation as authoritative", COLLECTOR_PROMPT)
        self.assertIn("sniff_run.verification.ready=true", COLLECTOR_PROMPT)
        self.assertIn("only completion signal", COLLECTOR_PROMPT)
        self.assertIn("collector_tap is locked until verification.ready=true", COLLECTOR_PROMPT)
        self.assertIn("commonly labeled 点菜, 商品, or 全部商品", COLLECTOR_PROMPT)
        self.assertIn("Do not evade a failed batch score by scrolling elsewhere", COLLECTOR_PROMPT)
        self.assertNotIn("Define run(xml: str) -> dict", COLLECTOR_PROMPT)
        self.assertNotIn("Call eval_collection", COLLECTOR_PROMPT)
        self.assertIn("Do not enter a lower-level page", COLLECTOR_PROMPT)

    def test_collector_coder_prompt_owns_code_and_patch_contract(self) -> None:
        self.assertIn("persistent coding subagent", COLLECTOR_CODER_PROMPT)
        self.assertIn("all XML/r1 pairs", COLLECTOR_CODER_PROMPT)
        self.assertIn("read_run_agent_py", COLLECTOR_CODER_PROMPT)
        self.assertIn("apply_run_agent_py_patch", COLLECTOR_CODER_PROMPT)
        self.assertIn("no device", COLLECTOR_CODER_PROMPT)
        self.assertIn("Define run(xml: str) -> dict", COLLECTOR_CODER_PROMPT)
        self.assertIn("Patch grammar:", COLLECTOR_CODER_PROMPT)
        self.assertIn(RUN_AGENT_PY_PATCH_GRAMMAR, COLLECTOR_CODER_PROMPT)
        self.assertIn("exactly one update_hunk with filename run.agent.py", COLLECTOR_CODER_PROMPT)
        self.assertIn("shared UI template", COLLECTOR_CODER_PROMPT)
        self.assertIn("same-width list containers", COLLECTOR_CODER_PROMPT)
        self.assertIn("must not be selectors or branch conditions", COLLECTOR_CODER_PROMPT)
        self.assertIn("Do not append one branch per example", COLLECTOR_CODER_PROMPT)
        self.assertIn("Never design toward the supplied cases", COLLECTOR_CODER_PROMPT)
        self.assertIn("Do not create parallel modules", COLLECTOR_CODER_PROMPT)
        self.assertIn("Keep function signatures narrow", COLLECTOR_CODER_PROMPT)
        self.assertIn("Do not add configuration flags", COLLECTOR_CODER_PROMPT)
        self.assertIn("Remove obsolete branches", COLLECTOR_CODER_PROMPT)
        self.assertIn("duplicated responsibilities, meaningless parameters", COLLECTOR_CODER_PROMPT)
        self.assertNotIn("ScriptBudgetExceeded", COLLECTOR_CODER_PROMPT)
        self.assertNotIn("12,000-byte", COLLECTOR_CODER_PROMPT)
        self.assertIn("probable structural failure mechanism", COLLECTOR_PROMPT)
        self.assertIn("Each XML snapshot is processed independently", COLLECTOR_CODER_PROMPT)
        self.assertIn("compares complete JSON results", COLLECTOR_CODER_PROMPT)
        self.assertIn("product extraction must not require top-of-page nodes", COLLECTOR_CODER_PROMPT)
        self.assertIn("one malformed card does not fail the page", COLLECTOR_CODER_PROMPT)
        self.assertIn("Cross-snapshot persistence and deduplication are handled outside", COLLECTOR_CODER_PROMPT)
        self.assertIn("deduplicate by stable extracted business fields", COLLECTOR_CODER_PROMPT)
        self.assertIn("Never use node addresses, dynamic IDs, or case metadata as keys", COLLECTOR_CODER_PROMPT)
        self.assertIn("collect valid descendant text fragments", COLLECTOR_CODER_PROMPT)
        self.assertIn("Do not stop at the first text node", COLLECTOR_CODER_PROMPT)
        self.assertIn("Never use unrestricted global text search", COLLECTOR_CODER_PROMPT)
        self.assertIn("Do not claim success before", COLLECTOR_CODER_PROMPT)
        self.assertIn("every previously encountered page instance", COLLECTOR_CODER_PROMPT)
        self.assertIn("English module docstring", COLLECTOR_CODER_PROMPT)
        self.assertIn("high-level structural recognition basis", COLLECTOR_CODER_PROMPT)
        self.assertIn("not implementation steps", COLLECTOR_CODER_PROMPT)
        self.assertIn("genuine special scenario", COLLECTOR_CODER_PROMPT)
        self.assertIn("structural trigger", COLLECTOR_CODER_PROMPT)


if __name__ == "__main__":
    unittest.main()
