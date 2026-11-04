from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.prompts import (
    load_prompt,
    RUN_AGENT_PY_PATCH_GRAMMAR,
)
from Reco.model_interface import INSTRUCTIONS

CONDUCTOR_PROMPT = load_prompt("conductor", None) + "\nPage reading tools:\n" + INSTRUCTIONS
COLLECTOR_PROMPT = "\n".join((load_prompt("collector", None), RUN_AGENT_PY_PATCH_GRAMMAR, INSTRUCTIONS))


class SystemPromptTests(unittest.TestCase):
    def test_conductor_prompt_exposes_new_tool_contract(self) -> None:
        self.assertIn(
            "Device: run_package, close_package, page_view, screenshot, tap_in_page, navigate_to, navigate_back, swipe_up, swipe_up_long, swipe_down",
            CONDUCTOR_PROMPT,
        )
        self.assertNotIn("Page docs:", CONDUCTOR_PROMPT)
        self.assertIn("Collector: spawn_subagent", CONDUCTOR_PROMPT)
        self.assertNotIn("query_manual", CONDUCTOR_PROMPT)
        self.assertNotIn("Excel", CONDUCTOR_PROMPT)
        self.assertNotIn("prepare_collector", CONDUCTOR_PROMPT)

    def test_conductor_prompt_requires_explicit_navigation_and_runtime_manuals(self) -> None:
        self.assertIn("confirm whether the page matches", CONDUCTOR_PROMPT)
        self.assertIn(
            "latest `<current_page_manual>` message is the authoritative manual", CONDUCTOR_PROMPT
        )
        self.assertIn("Use tap_in_page only", CONDUCTOR_PROMPT)
        self.assertIn("Use navigate_to", CONDUCTOR_PROMPT)
        self.assertIn("Use navigate_back", CONDUCTOR_PROMPT)
        self.assertIn("A collector is page-bound", CONDUCTOR_PROMPT)
        self.assertIn("Before spawning, query list_subagents", CONDUCTOR_PROMPT)
        self.assertIn("without task.md operation steps", CONDUCTOR_PROMPT)

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
        self.assertIn("call report(task_id", CONDUCTOR_PROMPT)
        self.assertIn("No final JSON message is needed", CONDUCTOR_PROMPT)

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

    def test_collector_reuses_structure_before_coding_and_scopes_invalidation(self) -> None:
        for instruction in (
            "multiple complementary semantic anchors",
            "relation",
            "Do not preemptively rewrite or read a script",
            "skip all script/guide optimization",
            "annotation logic is the ONLY exception",
            "including this visit's samples",
            "only extraction code changes, keep every existing case",
            "CURRENT merchant",
            "Do not rewrite an equivalent guide",
        ):
            self.assertIn(instruction, COLLECTOR_PROMPT)


if __name__ == "__main__":
    unittest.main()
