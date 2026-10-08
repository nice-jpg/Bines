from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from prompts import (
    COLLECTOR_PROMPT,
    RUN_AGENT_PY_PATCH_GRAMMAR,
    SYSTEM_PROMPT,
)


class SystemPromptTests(unittest.TestCase):
    def test_conductor_prompt_exposes_new_tool_contract(self) -> None:
        self.assertIn(
            "Device: run_package, close_package, page_view, screenshot, tap_in_page, navigate_to, navigate_back, swipe_up, swipe_up_long, swipe_down",
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
        self.assertIn("Do not inspect extraction output or repair scripts", SYSTEM_PROMPT)
        self.assertIn("do not call is_done after ordinary swipes", SYSTEM_PROMPT)
        self.assertIn("wait for new possible-end evidence", SYSTEM_PROMPT)
        self.assertIn("completed result uses the same three-zero-swipe completion rule", SYSTEM_PROMPT)
        self.assertIn("Collection and persistence run silently", SYSTEM_PROMPT)
        self.assertIn("must not extract information yourself", SYSTEM_PROMPT)

    def test_conductor_prompt_requires_dangerous_tool_justification(self) -> None:
        self.assertIn("run_package and close_package are dangerous operations", SYSTEM_PROMPT)
        self.assertIn("risk_acknowledged=true", SYSTEM_PROMPT)
        self.assertIn("page tasks, collection, and archival are complete", SYSTEM_PROMPT)
        self.assertIn("Never use an operation-only or templated placeholder", SYSTEM_PROMPT)

    def test_conductor_prompt_requires_persistent_structured_completion(self) -> None:
        self.assertIn('"resolved": true', SYSTEM_PROMPT)
        self.assertIn('"next_action": null', SYSTEM_PROMPT)
        self.assertIn("persistence layer will retain the existing context", SYSTEM_PROMPT)
        self.assertIn("validates both the structured response and deterministic graph state", SYSTEM_PROMPT)

    def test_collector_owns_visual_validation_and_script_repair(self) -> None:
        for instruction in (
            "run(view)", "save_baseline", "screenshot", "read_collection_guide",
            "apply_run_agent_py_patch", "collector_swipe_to_random",
            "Baseline correction is allowed only through repair_baseline", "capture_validation_case",
            "save_baseline stores evidence, not a test result",
            "English docstrings", "stagnation_review_required=true",
        ):
            self.assertIn(instruction, COLLECTOR_PROMPT)
        self.assertIn(RUN_AGENT_PY_PATCH_GRAMMAR, COLLECTOR_PROMPT)
        self.assertNotIn("consult_coder", COLLECTOR_PROMPT)


if __name__ == "__main__":
    unittest.main()
