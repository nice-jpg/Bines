"""Model adapters consume an initialized immutable configuration service."""

from unittest.mock import Mock

import pytest
from pydantic import SecretStr

from src.configuration import models as model
from src.configuration.manager import ConfigurationManager
from src.configuration.errors import ConfigurationError
from src.configuration.loader import legacy_configuration, parse_configuration


def test_model_legacy_defaults(configuration_service):
    configuration_service._snapshot = legacy_configuration(
        {
            "MODEL_PROVIDER": "deepseek",
            "DEEPSEEK_BASE_URL": "https://example.test/v1",
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_MODEL": "test-model",
        }
    )
    config = ConfigurationManager.instance().get_model()
    assert (config.provider, config.model, config.api_key) == ("deepseek", "test-model", "test-key")


def test_build_model_passes_config(monkeypatch, configuration_service):
    configuration_service._snapshot = legacy_configuration(
        {
            "MODEL_PROVIDER": "deepseek",
            "DEEPSEEK_BASE_URL": "https://example.test/v1",
            "DEEPSEEK_API_KEY": "test-key",
            "DEEPSEEK_MODEL": "test-model",
        }
    )
    constructor = Mock()
    monkeypatch.setattr(model, "ChatOpenAI", constructor)
    assert model.get_model_instance() is constructor.return_value
    constructor.assert_called_once_with(
        base_url="https://example.test/v1",
        api_key=SecretStr("test-key"),
        model="test-model",
        streaming=False,
        use_responses_api=False,
        metadata={"prompt_model": "test-model", "prompt_affinity": None},
    )


def test_build_codex_model(monkeypatch, configuration_service):
    configuration_service._snapshot = legacy_configuration(
        {
            "MODEL_PROVIDER": "codex",
            "CODEX_MODEL": "gpt-configured",
            "CODEX_REASONING_EFFORT": "high",
            "CODEX_STREAM_MAX_RETRIES": "7",
        }
    )
    constructor = Mock()
    monkeypatch.setattr("src.codex.create_chat_model", constructor)
    assert model.get_model_instance() is constructor.return_value
    constructor.assert_called_once_with(
        model="gpt-configured", reasoning_effort="high", stream_max_retries=7
    )
    assert constructor.return_value.metadata == {"prompt_model": "gpt-configured", "prompt_affinity": None}


@pytest.mark.parametrize("value", [-1, True, "5"])
def test_invalid_codex_retry_count(value, configuration_service):
    configuration_service._snapshot = parse_configuration(
        {
            "schema_version": 1,
            "model": {
                "providers": {
                    "test": {"provider": "codex", "model": "test", "options": {"stream_max_retries": value}}
                }
            },
        }
    )
    with pytest.raises(ConfigurationError, match="non-negative integer"):
        model.get_model_instance()


def test_new_provider_uses_explicit_api_fields(configuration_service):
    configuration_service._snapshot = legacy_configuration(
        {
            "MODEL_PROVIDER": "new_provider",
            "NEW_PROVIDER_BASE_URL": "https://example.test/v1",
            "NEW_PROVIDER_API_KEY": "key",
            "NEW_PROVIDER_MODEL": "model",
        }
    )
    assert ConfigurationManager.instance().get_model().provider == "new_provider"


def test_missing_provider_fields_rejected():
    with pytest.raises(ConfigurationError):
        legacy_configuration({"MODEL_PROVIDER": "unknown"})


def test_model_rules_create_independent_instances(configuration_service):
    configuration_service._snapshot = parse_configuration({
        "schema_version": 1,
        "model": {
            "default": "primary", "coding": "code", "behave": "action",
            "providers": {
                name: {"provider": "openai", "model": name,
                       "base_url": "https://example.test/v1", "api_key": "test"}
                for name in ("primary", "code", "action")
            },
        },
    })
    from src.configuration import get_model_instance

    first = get_model_instance("coding")
    second = get_model_instance("coding")
    assert first is not second
    assert first.root_client is not second.root_client
    assert first.model_name == second.model_name == "code"
    assert get_model_instance().model_name == "primary"
    assert get_model_instance("behave").model_name == "action"


def test_missing_rules_fall_back_without_sharing_codex_transport(configuration_service, monkeypatch):
    configuration_service._snapshot = parse_configuration({
        "schema_version": 1,
        "model": {"default": "primary", "providers": {
            "primary": {"provider": "codex", "model": "test"},
        }},
    })
    from src.codex import OpenAICodexModel
    from src.configuration import get_model_instance

    monkeypatch.setattr("src.codex.create_chat_model", lambda **kwargs: OpenAICodexModel(**kwargs))
    models = [get_model_instance(rule) for rule in (None, "coding", "behave")]
    assert len({id(instance) for instance in models}) == 3
    models[0]._connection_response_id = "root-response"
    assert models[1]._connection_response_id is None
    assert models[2]._connection_response_id is None
    for instance in models:
        model.close_model(instance)


@pytest.mark.parametrize("rule", ["coding", "behave"])
def test_invalid_model_rule_reference_rejected(rule):
    with pytest.raises(ConfigurationError, match="does not exist"):
        parse_configuration({"schema_version": 1, "model": {rule: "missing"}})


def test_unknown_model_rule_rejected(configuration_service):
    with pytest.raises(ConfigurationError, match="Model rule"):
        model.get_model_instance("unknown")


def test_engine_releases_its_model_once(monkeypatch):
    from engine_helpers import ScriptedEngine
    from src.codex import OpenAICodexModel

    closed = []
    monkeypatch.setattr(OpenAICodexModel, "close", lambda self: closed.append(self))
    instance = OpenAICodexModel(model="test")
    engine = ScriptedEngine(model=instance)
    engine.close()
    engine.close()
    assert closed == [instance]


def test_engine_construction_failure_releases_model(monkeypatch):
    from engine_helpers import ScriptedEngine
    from src.codex import OpenAICodexModel

    closed = []
    monkeypatch.setattr(OpenAICodexModel, "close", lambda self: closed.append(self))
    monkeypatch.setattr(ScriptedEngine, "_build_graph", Mock(side_effect=ValueError("invalid graph")))
    instance = OpenAICodexModel(model="test")
    with pytest.raises(ValueError, match="invalid graph"):
        ScriptedEngine(model=instance)
    assert closed == [instance]


def test_assistant_children_request_behave_and_default_models(monkeypatch):
    from types import SimpleNamespace
    from engine_helpers import Model
    from src.agent_hypervisor.host import Host
    from src.application.assistant import AssistantEngine

    host = Host()
    parent_model = Model(replies=[])
    root = AssistantEngine(model=parent_model, handle=host.register_root())
    constructor = Mock(side_effect=lambda *args: Model(replies=[]))
    monkeypatch.setattr("src.application.assistant.get_model_instance", constructor)
    started = []
    def start(engine):
        started.append(engine)
        host._workers[engine.handle.agent_id] = engine
    monkeypatch.setattr(host, "start", start)
    monkeypatch.setattr("src.application.assistant.ConductorEngine", lambda **kwargs: SimpleNamespace(**kwargs))
    try:
        root.spawn_subagent("conductor")
        constructor.assert_called_once_with("behave")
        assert started[0].model is not parent_model
        with host.child(root.handle.agent_id) as handle:
            fork = root._create_fork(handle)
            host.start(fork)
            try:
                constructor.assert_called_with()
                assert fork._model is not parent_model
                assert fork._model is not started[0].model
            finally:
                fork.close()
    finally:
        root.close()
