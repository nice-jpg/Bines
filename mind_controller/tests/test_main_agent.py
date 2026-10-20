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


def test_default_model_initializes_configuration_before_build(monkeypatch):
    from mind_controller_agent.main_agent import _build_default_model
    from src.configuration.manager import ConfigurationManager

    calls = []
    monkeypatch.setattr(ConfigurationManager, 'initialize', lambda: calls.append('configuration'))
    monkeypatch.setattr('src.application.trace.trace_client', lambda: calls.append('trace'))
    model = object()
    monkeypatch.setattr('src.application.model.build_model', lambda: calls.append('model') or model)
    assert _build_default_model() is model
    assert calls == ['configuration', 'trace', 'model']
