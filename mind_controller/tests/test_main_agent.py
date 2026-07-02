from __future__ import annotations

from types import SimpleNamespace

from mind_controller_agent.main_agent import _supports_freeform_patch_tool


def test_deepseek_style_chat_endpoint_disables_freeform_patch() -> None:
    model = SimpleNamespace(
        _llm_type="openai-chat",
        use_responses_api=False,
        openai_api_base="https://api.deepseek.com",
    )

    assert _supports_freeform_patch_tool(model) is False


def test_codex_websocket_supports_freeform_patch() -> None:
    model = SimpleNamespace(
        _llm_type="codex-websocket",
        use_responses_api=None,
        openai_api_base=None,
    )

    assert _supports_freeform_patch_tool(model) is True
