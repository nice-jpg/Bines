"""Model-scoped assets, per-file fallback, and engine integration."""

import pytest

from src.configuration import get_model_instance
from src.configuration.loader import parse_configuration
from src.prompts import loader


@pytest.fixture
def prompt_tree(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "PROMPT_ROOT", tmp_path)
    for model in ("gpt-6.1-sol", "gpt-6.1-astra", "deepseek-v4.1-flash"):
        (tmp_path / model).mkdir()
    (tmp_path / "gpt-6.1-sol/root.md").write_text("baseline")
    (tmp_path / "gpt-6.1-sol/communication.md").write_text("communication baseline")
    (tmp_path / "gpt-6.1-astra/root.md").write_text("astra root")
    return tmp_path


def test_page_mechanism_selects_whole_tree_and_keeps_writes_in_it(prompt_tree):
    from src.page.mechanism import PageMechanism

    profile = {"prompt_model": "deepseek-v4.1-flash", "prompt_affinity": "gpt-6.1-astra"}
    baseline = prompt_tree / "gpt-6.1-sol/page_mechanism"
    baseline.mkdir()
    assert loader.resolve_page_mechanism(profile) == baseline
    affinity = prompt_tree / "gpt-6.1-astra/page_mechanism"
    affinity.mkdir()
    assert loader.resolve_page_mechanism(profile) == affinity
    native = prompt_tree / "deepseek-v4.1-flash/page_mechanism"
    native.mkdir()
    assert loader.resolve_page_mechanism(profile) == native
    for root in (baseline, affinity, native):
        (root / "meituan").mkdir()
        (root / "meituan/PAGE.md").write_text("page")
        (root / "meituan/task.md").write_text("task")
    mechanism = PageMechanism(loader.resolve_page_mechanism(profile))
    mechanism.update_page_notes("meituan", "native observation")
    assert "native observation" in (native / "meituan/PAGE.md").read_text()
    assert (affinity / "meituan/PAGE.md").read_text() == "page"
    assert (baseline / "meituan/PAGE.md").read_text() == "page"
    # An incomplete override must not mix documents from another model.
    (native / "meituan/task.md").unlink()
    with pytest.raises(FileNotFoundError):
        mechanism.load_manual_bundle("meituan")


def test_page_mechanism_rejects_escape_and_missing_tree(prompt_tree):
    with pytest.raises(FileNotFoundError):
        loader.resolve_page_mechanism(None)
    with pytest.raises(ValueError):
        loader.resolve_page_mechanism({"prompt_affinity": "../outside"})
    (prompt_tree / "gpt-6.1-sol/page_mechanism").symlink_to(prompt_tree.parent, target_is_directory=True)
    with pytest.raises(ValueError, match="within"):
        loader.resolve_page_mechanism(None)


def test_current_model_then_affinity_then_baseline(prompt_tree):
    profile = {"prompt_model": "deepseek-v4.1-flash", "prompt_affinity": "gpt-6.1-astra"}
    assert loader.load_prompt("root", profile) == "astra root"
    assert loader.load_prompt("communication", profile) == "communication baseline"
    (prompt_tree / "deepseek-v4.1-flash/root.md").write_text("native root")
    assert loader.load_prompt("root", profile) == "native root"
    assert loader.load_prompt("root", {"prompt_model": "unknown"}) == "baseline"


def test_empty_override_is_not_silently_ignored(prompt_tree):
    (prompt_tree / "gpt-6.1-astra/root.md").write_text(" ")
    with pytest.raises(ValueError, match="empty"):
        loader.load_prompt("root", {"prompt_model": "gpt-6.1-astra"})


@pytest.mark.parametrize("name", ["../secret", "/tmp", "..", "", "a\\b"])
def test_paths_are_confined(prompt_tree, name):
    with pytest.raises(ValueError):
        loader.load_prompt("root", {"prompt_model": name})
    with pytest.raises(ValueError):
        loader.load_prompt(name, None)


def test_symlink_cannot_escape(prompt_tree, tmp_path):
    outside = tmp_path.parent / "outside-prompts"
    outside.mkdir(exist_ok=True)
    (prompt_tree / "escape").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="within"):
        loader.load_prompt("root", {"prompt_model": "escape"})


def test_configuration_profile_reaches_engine(prompt_tree, configuration_service):
    from gateway_helpers import GatewayHandle, RecordingChannel
    from src.agent_hypervisor.root import RootAgent

    configuration_service._snapshot = parse_configuration({
        "schema_version": 1, "trace": {"enabled": False},
        "model": {"providers": {"test": {
            "provider": "openai", "model": "deepseek-v4.1-flash", "affinity": "gpt-6.1-astra",
            "base_url": "https://example.test/v1", "api_key": "test",
        }}},
    })
    model = get_model_instance()
    root = RootAgent(model=model, handle=GatewayHandle(RecordingChannel()).handle)
    try:
        assert root._system_prompt() == "astra root"
        assert root._prompt("communication") == "communication baseline"
        assert configuration_service.get_model().affinity == "gpt-6.1-astra"
    finally:
        root.close()


@pytest.mark.parametrize("affinity", ["", 3, True])
def test_affinity_requires_nonempty_string(affinity):
    from src.configuration.errors import ConfigurationError

    with pytest.raises(ConfigurationError):
        parse_configuration({"schema_version": 1, "model": {"providers": {
            "test": {"provider": "codex", "model": "test", "affinity": affinity}
        }}})
