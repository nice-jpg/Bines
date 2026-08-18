from nice_dumper_agent.prompts import (
    MAIN_SYSTEM_PROMPT,
    build_optimizer_feedback_prompt,
)


def test_main_prompt_preserves_tree_while_allowing_container_contraction() -> None:
    prompt = " ".join(MAIN_SYSTEM_PROMPT.split())

    assert "Preserve the original ancestor branch and document order" in prompt
    assert "splice its retained children into the container's original position" in prompt
    assert "This edge contraction is allowed" in prompt
    assert "it may never cross branches" in prompt
    assert "Do not collect all useful nodes into one flat" in prompt
    assert "Prune bottom-up" in prompt
    assert "single-child generic containers first" in prompt


def test_main_prompt_demands_direct_character_cleanup() -> None:
    prompt = " ".join(MAIN_SYSTEM_PROMPT.split())

    assert "Delete meaningless characters directly" in prompt
    assert "try removing the XML declaration" in prompt
    assert "empty attributes" in prompt
    assert "Prefer a small evidence-based allowlist" in prompt
    assert "recursively clear whitespace-only element.text" in prompt
    assert "every element.tail" in prompt
    assert "ET.tostring alone does not remove" in prompt
    assert "do not create a header/top-screen exception" in prompt
    assert "returned when optimize(xml_text)" in prompt
    assert "does not apply to the generated Python source" in prompt
    assert "Keep optimize_xml.py readable" in prompt
    assert "First try dropping index, package" in prompt
    assert "no individual metric is required to reach zero" in prompt


def test_main_prompt_keeps_exploring_before_rollback() -> None:
    assert "Do not abandon an algorithm after one lower score" in MAIN_SYSTEM_PROMPT
    assert "stale_rounds consecutive evaluated attempts" in MAIN_SYSTEM_PROMPT
    assert "best evaluated checkpoint" in MAIN_SYSTEM_PROMPT
    assert "unscored experimental script" in MAIN_SYSTEM_PROMPT


def test_feedback_prompt_repeats_structure_and_exploration_contract() -> None:
    prompt = build_optimizer_feedback_prompt(
        xml0="<hierarchy><node/></hierarchy>",
        xml1="<hierarchy/>",
        l0_json='{"functions": []}',
        l1_json='{"functions": []}',
        score_json='{"score": 0}',
        optimizer_source="def optimize(xml_text): return xml_text",
        history_summary="round 1 regressed",
    )

    assert "Preserve every direct parent-child edge" in prompt
    assert "Remove meaningless XML characters" in prompt
    assert "temporary regression" in prompt
    assert "continue/refine/rollback" in prompt
    assert "remaining compaction_metrics" in prompt
    assert "not the Python source code" in prompt
    assert "ranked opportunities rather than pass/fail gates" in prompt
    assert "promote its children in place only along their original ancestor" in prompt
    assert "contractible-container counts" in prompt
