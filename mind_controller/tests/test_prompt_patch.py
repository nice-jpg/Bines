from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mind_controller_agent.prompt_patch import (
    APPLY_PATCH_GRAMMAR,
    FUNCTION_APPLY_PATCH_DESCRIPTION,
    MINIMAL_PATCH_TEMPLATE,
    PromptPatchError,
    PromptPatchInput,
    apply_file_update,
    parse_prompt_patch,
)


def test_contract_is_update_only_and_function_input_is_explicit() -> None:
    schema = PromptPatchInput.model_json_schema()

    assert "*** Update File:" in APPLY_PATCH_GRAMMAR
    assert "*** Add File:" not in APPLY_PATCH_GRAMMAR
    assert "*** Delete File:" not in APPLY_PATCH_GRAMMAR
    assert "*** Move to:" not in APPLY_PATCH_GRAMMAR
    assert schema["required"] == ["input"]
    assert schema["additionalProperties"] is False
    assert "complete raw Update-only" in schema["properties"]["input"]["description"]
    assert '{"input": "<the complete raw patch text>"}' in (
        FUNCTION_APPLY_PATCH_DESCRIPTION
    )
    assert "Markdown bullet `- old rule`" in FUNCTION_APPLY_PATCH_DESCRIPTION

    with pytest.raises(ValidationError):
        PromptPatchInput.model_validate({"patch": MINIMAL_PATCH_TEMPLATE})


def test_parser_accepts_first_hunk_without_marker_and_blank_chunk_separator() -> None:
    update = parse_prompt_patch(
        """*** Begin Patch
*** Update File: prompt.md
-one
+ONE

@@ section
-two
+TWO
*** End Patch"""
    )[0]

    result = apply_file_update(
        "one\nsection\ntwo\n",
        update,
        path=Path("prompt.md"),
    )

    assert result == "ONE\nsection\nTWO\n"


def test_parser_accepts_boundary_whitespace_and_heredoc() -> None:
    update = parse_prompt_patch(
        """<<'EOF'
  *** Begin Patch
*** Update File: prompt.md
@@
-old
+new
  *** End Patch
EOF"""
    )[0]

    assert apply_file_update("old", update, path=Path("prompt.md")) == "new"


def test_end_of_file_and_unicode_context_matching() -> None:
    update = parse_prompt_patch(
        """*** Begin Patch
*** Update File: prompt.md
@@ section
-"old"
+"new"
*** End of File
*** End Patch"""
    )[0]

    result = apply_file_update(
        "section\n  “old”   \n",
        update,
        path=Path("prompt.md"),
    )

    assert result == 'section\n"new"\n'


def test_inexact_anchor_falls_back_to_unique_old_lines() -> None:
    update = parse_prompt_patch(
        """*** Begin Patch
*** Update File: system_prompt.py
@@ Critical Excel integrity rules
-- **Never rename Sheet1**
+- **Do not rename Sheet1**
*** End Patch"""
    )[0]

    result = apply_file_update(
        "Critical Excel integrity rules:\n- **Never rename Sheet1**\n",
        update,
        path=Path("system_prompt.py"),
    )

    assert result == (
        "Critical Excel integrity rules:\n- **Do not rename Sheet1**\n"
    )


def test_inexact_anchor_does_not_apply_ambiguous_old_lines() -> None:
    update = parse_prompt_patch(
        """*** Begin Patch
*** Update File: prompt.md
@@ Missing heading
-same
+changed
*** End Patch"""
    )[0]

    with pytest.raises(PromptPatchError) as captured:
        apply_file_update(
            "same\nsection\nsame\n",
            update,
            path=Path("prompt.md"),
        )

    assert captured.value.error_code == "context_anchor_not_found"
    assert "exact prompt line" in str(captured.value)


def test_anchor_may_be_the_first_line_being_replaced() -> None:
    update = parse_prompt_patch(
        """*** Begin Patch
*** Update File: workspace/patch.txt
@@ 3. After every evaluation, call should_stop.
-3. After every evaluation, call should_stop.
+Hello World
*** End Patch"""
    )[0]

    result = apply_file_update(
        "2. Establish a baseline.\n"
        "3. After every evaluation, call should_stop.\n"
        "4. If stop=false, diagnose.\n",
        update,
        path=Path("workspace/patch.txt"),
    )

    assert result == (
        "2. Establish a baseline.\n"
        "Hello World\n"
        "4. If stop=false, diagnose.\n"
    )


@pytest.mark.parametrize(
    ("patch", "error_code"),
    [
        ("```patch\n*** Begin Patch\n*** End Patch\n```", "markdown_wrapper"),
        (
            "*** Begin Patch\n*** Add File: prompt.md\n+text\n*** End Patch",
            "unsupported_operation",
        ),
        (
            "*** Begin Patch\n*** Update File: /tmp/prompt.md\n@@\n-a\n+b\n*** End Patch",
            "absolute_path",
        ),
        (
            "*** Begin Patch\n*** Update File: prompt.md\nbad\n*** End Patch",
            "missing_hunk_header",
        ),
    ],
)
def test_parser_returns_stable_actionable_errors(
    patch: str,
    error_code: str,
) -> None:
    with pytest.raises(PromptPatchError) as captured:
        parse_prompt_patch(patch)

    assert captured.value.error_code == error_code
    assert str(captured.value)


def test_context_failure_reports_expected_lines_and_reread_hint() -> None:
    update = parse_prompt_patch(
        """*** Begin Patch
*** Update File: prompt.md
@@
-stale text
+new text
*** End Patch"""
    )[0]

    with pytest.raises(PromptPatchError) as captured:
        apply_file_update("current text", update, path=Path("prompt.md"))

    assert captured.value.error_code == "context_not_found"
    assert captured.value.expected == "stale text"
    assert "read_prompt" in str(captured.value)
