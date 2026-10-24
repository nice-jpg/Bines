from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.prompts import (
    COLLECTOR_PROMPT,
    CONDUCTOR_PROMPT,
    RUN_AGENT_PY_PATCH_GRAMMAR,
)


class SystemPromptTests(unittest.TestCase):
    def test_conductor_prompt_exposes_new_tool_contract(self) -> None:
        self.assertIn(
            "Device: run_package, close_package, page_view, screenshot, tap_in_page, navigate_to, navigate_back, swipe_up, swipe_up_long, swipe_down",
            CONDUCTOR_PROMPT,
        )
        self.assertNotIn("Page docs:", CONDUCTOR_PROMPT)
        self.assertIn("Collector: prepare_collector", CONDUCTOR_PROMPT)
        self.assertNotIn("query_manual", CONDUCTOR_PROMPT)
        self.assertNotIn("Excel", CONDUCTOR_PROMPT)
        self.assertNotIn("spawn_subagent", CONDUCTOR_PROMPT)

    def test_conductor_prompt_requires_explicit_navigation_and_runtime_manuals(self) -> None:
        self.assertIn("Only after confirm_page accepts your conclusion", CONDUCTOR_PROMPT)
        self.assertIn(
            "latest `<current_page_manual>` system message is the authoritative manual", CONDUCTOR_PROMPT
        )
        self.assertIn("Use tap_in_page only", CONDUCTOR_PROMPT)
        self.assertIn("Use navigate_to", CONDUCTOR_PROMPT)
        self.assertIn("Use navigate_back", CONDUCTOR_PROMPT)
        self.assertIn("Each merchant visit requires prepare_collector", CONDUCTOR_PROMPT)
        self.assertIn("Do not delegate task.md operation steps", CONDUCTOR_PROMPT)

    def test_conductor_prompt_forbids_direct_collection(self) -> None:
        self.assertIn("Do not collect merchant/product fields directly", CONDUCTOR_PROMPT)
        self.assertIn("Do not inspect extraction output or repair scripts", CONDUCTOR_PROMPT)
        self.assertIn("call is_done only when exhaustion seems likely", CONDUCTOR_PROMPT)
        self.assertIn("done=false requires further collection", CONDUCTOR_PROMPT)
        self.assertIn(
            "Both use three consecutive complete zero-new-item swipes", CONDUCTOR_PROMPT
        )
        self.assertIn("Collection and persistence run silently", CONDUCTOR_PROMPT)
        self.assertIn("must not extract information yourself", CONDUCTOR_PROMPT)

    def test_conductor_prompt_requires_dangerous_tool_justification(self) -> None:
        self.assertIn("run_package and close_package are dangerous operations", CONDUCTOR_PROMPT)
        self.assertIn("risk_acknowledged=true", CONDUCTOR_PROMPT)
        self.assertIn("Completion authorization is not required", CONDUCTOR_PROMPT)
        self.assertIn("Never use an operation-only or templated placeholder", CONDUCTOR_PROMPT)

    def test_conductor_prompt_requires_persistent_structured_completion(self) -> None:
        self.assertIn('"resolved": true', CONDUCTOR_PROMPT)
        self.assertIn('"next_action": null', CONDUCTOR_PROMPT)
        self.assertIn("persistence layer will retain the existing context", CONDUCTOR_PROMPT)
        self.assertIn("validates the structured response, not business completion state", CONDUCTOR_PROMPT)

    def test_collector_owns_visual_validation_and_script_repair(self) -> None:
        for instruction in (
            "run(view)",
            "save_baseline",
            "screenshot",
            "read_collection_guide",
            "apply_run_agent_py_patch",
            "collector_swipe_to_random",
            "Baseline correction is allowed only through repair_baseline",
            "capture_validation_case",
            "save_baseline stores evidence, not a test result",
            "English docstrings",
            "stagnation_review_required=true",
        ):
            self.assertIn(instruction, COLLECTOR_PROMPT)
        self.assertIn(RUN_AGENT_PY_PATCH_GRAMMAR, COLLECTOR_PROMPT)
        self.assertNotIn("consult_coder", COLLECTOR_PROMPT)


if __name__ == "__main__":
    unittest.main()
