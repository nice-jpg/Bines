"""Configuration contracts, including real singleton lifecycle in fresh processes."""

import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier, Event, Thread
from unittest.mock import Mock

import pytest

from src.application.trace import trace_client
from src.configuration.errors import ConfigurationError, ConfigurationKeyError
from src.configuration.loader import legacy_configuration, load_configuration, parse_configuration
from src.configuration.locking import ReadWriteLock


def subprocess_check(body, directory):
    script = (
        "from pathlib import Path\nfrom src.configuration import loader\nloader.WORKSPACE_ROOT = Path("
        + repr(str(directory))
        + ")\n"
        + textwrap.dedent(body)
    )
    result = subprocess.run([sys.executable, "-c", script], text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_singleton_publication_once_and_snapshot_is_stable(tmp_path):
    (tmp_path / "config.toml").write_text("schema_version = 1\n[other]\nvalue = 7\n")
    subprocess_check(
        """
        from src.configuration.manager import ConfigurationManager as Manager
        from src.configuration.errors import ConfigurationNotInitialized, ConfigurationError
        try:
            Manager.instance()
        except ConfigurationNotInitialized:
            pass
        else:
            raise AssertionError('implicitly initialized')
        try:
            Manager()
        except ConfigurationError:
            pass
        else:
            raise AssertionError('direct construction accepted')
        Manager.initialize()
        service = Manager.instance()
        (loader.WORKSPACE_ROOT / 'config.toml').write_text('invalid toml!')
        Manager.initialize()
        assert Manager.instance() is service
        assert service.get_other('value') == 7
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert all(value == 7 for value in pool.map(lambda _: Manager.instance().get_other('value'), range(30)))
    """,
        tmp_path,
    )


def test_failed_and_worker_initialization_never_publish(tmp_path):
    (tmp_path / "config.toml").write_text('api_key = "sensitive-value"\ninvalid!')
    subprocess_check(
        """
        from src.configuration.manager import ConfigurationManager as Manager
        from src.configuration.errors import ConfigurationError, ConfigurationNotInitialized
        from concurrent.futures import ThreadPoolExecutor
        from traceback import format_exc
        for attempt in range(2):
            try:
                Manager.initialize()
            except ConfigurationError:
                assert 'sensitive-value' not in format_exc()
            else:
                raise AssertionError('invalid input accepted')
            try:
                Manager.instance()
            except ConfigurationNotInitialized:
                pass
            else:
                raise AssertionError('partial instance published')
        (loader.WORKSPACE_ROOT / 'config.toml').write_text('schema_version = 1')
        with ThreadPoolExecutor() as pool:
            error = pool.submit(Manager.initialize).exception()
            assert isinstance(error, ConfigurationError)
        Manager.initialize()
        with ThreadPoolExecutor() as pool:
            assert isinstance(pool.submit(Manager.initialize).exception(), ConfigurationError)
    """,
        tmp_path,
    )


def test_groups_defaults_and_deep_immutability(configuration_service):
    document = {
        "schema_version": 1,
        "model": {
            "default": "two",
            "providers": {
                "one": {"provider": "codex", "model": "m1"},
                "two": {"provider": "codex", "model": "m2", "options": {"nested": [{"number": 0}]}},
            },
        },
        "channel": {
            "feishu": {
                "default": "b",
                "connections": {"a": {"app_id": "a", "app_secret": "secret"}, "b": {"app_id": "b"}},
            }
        },
        "other": {"a.b": {"number": 0, "flag": False, "text": ""}},
        "trace": {"enabled": False},
    }
    configuration_service._snapshot = parse_configuration(document)
    config = configuration_service.get_model()
    assert config.model == "m2"
    assert configuration_service.get_model("one").model == "m1"
    assert configuration_service.get_channel("feishu")["app_id"] == "b"
    assert configuration_service.get_channel("feishu", "a")["app_id"] == "a"
    assert configuration_service.get_other("a.b", "number") == 0
    assert configuration_service.get_other("a.b", "flag") is False
    assert configuration_service.get_other("a.b", "text") == ""
    with pytest.raises(FrozenInstanceError):
        config.model = "changed"
    with pytest.raises(TypeError):
        config.options["nested"][0]["number"] = 3
    with pytest.raises(TypeError):
        configuration_service.get_channel("feishu")["app_id"] = "changed"
    document["model"]["providers"]["two"]["options"]["nested"][0]["number"] = 9
    assert config.options["nested"][0]["number"] == 0
    assert "secret" not in repr(configuration_service._snapshot)


def test_ambiguous_missing_and_invalid_query(configuration_service):
    configuration_service._snapshot = parse_configuration(
        {
            "schema_version": 1,
            "model": {"providers": {name: {"provider": "codex", "model": name} for name in ["a", "b"]}},
            "channel": {"feishu": {"connections": {"a": {}, "b": {}}}},
            "other": {"scalar": 3},
        }
    )
    with pytest.raises(ConfigurationError):
        configuration_service.get_model()
    with pytest.raises(ConfigurationError):
        configuration_service.get_channel("feishu")
    with pytest.raises(ConfigurationKeyError):
        configuration_service.get_model("missing")
    with pytest.raises(ConfigurationKeyError):
        configuration_service.get_other("missing")
    for path in [(), ("",), ("scalar", "child")]:
        with pytest.raises(ConfigurationError):
            configuration_service.get_other(*path)
    with pytest.raises(ConfigurationError):
        configuration_service.get_model("")


@pytest.mark.parametrize(
    "extra",
    [
        {"schema_version": True},
        {"schema_version": 2},
        {"typo": "secret"},
        {"trace": {"enabled": "false"}},
        {"trace": {"server_url": "not-a-url"}},
        {"model": {"default": "missing"}},
        {"model": {"providers": {"a": {"provider": "openai", "model": "m"}}}},
        {"channel": {"x": {"connections": {"a": {"number": 5}}}}},
        {"other": {"bad": float("nan")}},
        {"other": {"bad": None}},
    ],
)
def test_invalid_structures_rejected(extra):
    with pytest.raises(ConfigurationError):
        parse_configuration({"schema_version": 1, **extra})


def test_new_file_ignores_legacy_and_legacy_does_not_mutate_environment(tmp_path, monkeypatch):
    monkeypatch.setattr("src.configuration.loader.WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(os, "environ", {"FEISHU_APP_ID": "environment", "UNREGISTERED_SECRET": "secret"})
    (tmp_path / ".env").write_text(
        "FEISHU_APP_ID=file\nFEISHU_APP_SECRET=secret\nAPP_ID=alias\nNTRACE_ENABLED=off\n"
    )
    legacy = load_configuration()
    assert legacy.channels["feishu"]["default"]["app_id"] == "environment"
    assert not legacy.trace.enabled
    assert os.environ == {"FEISHU_APP_ID": "environment", "UNREGISTERED_SECRET": "secret"}
    assert "UNREGISTERED_SECRET" not in legacy.other
    (tmp_path / "config.toml").write_text("schema_version = 1\n[trace]\nenabled = true\n")
    modern = load_configuration()
    assert not modern.channels and modern.trace.enabled


def test_legacy_alias_order():
    snapshot = legacy_configuration({"LARK_APP_ID": "lark", "APP_ID": "app", "APP_SECRET": "secret"})
    assert snapshot.channels["feishu"]["default"]["app_id"] == "lark"


def test_shared_readers_and_exception_release():
    lock = ReadWriteLock()
    barrier = Barrier(3)

    def read():
        with lock.read_lock():
            barrier.wait(timeout=3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read)
        second = pool.submit(read)
        barrier.wait(timeout=3)
        first.result(timeout=3)
        second.result(timeout=3)
    with pytest.raises(ValueError):
        with lock.write_lock():
            raise ValueError("exit")
    with pytest.raises(ValueError):
        with lock.read_lock():
            raise ValueError("exit")
    with lock.write_lock():
        pass


def test_writer_waits_for_reader_and_excludes_other_readers():
    lock = ReadWriteLock()
    attempting = Event()
    acquired = Event()
    release = Event()
    read_done = Event()

    def write():
        attempting.set()
        with lock.write_lock():
            acquired.set()
            assert release.wait(3)

    def read():
        with lock.read_lock():
            read_done.set()

    writer = Thread(target=write)
    reader = Thread(target=read)
    with lock.read_lock():
        writer.start()
        assert attempting.wait(3)
        assert not acquired.wait(0.05)
        with lock._condition:
            assert lock._condition.wait_for(lambda: lock._waiting_writers > 0, timeout=3)
        reader.start()
        assert not read_done.wait(0.05)
    assert acquired.wait(3)
    assert not read_done.wait(0.05)
    release.set()
    writer.join(3)
    reader.join(3)
    assert not writer.is_alive() and not reader.is_alive() and read_done.is_set()


def test_trace_injection_uses_snapshot_not_environment(monkeypatch, configuration_service):
    configuration_service._snapshot = parse_configuration(
        {
            "schema_version": 1,
            "trace": {"enabled": False, "server_url": "https://trace.example.test"},
            "other": {"trace": {"request_timeout": 8}},
        }
    )
    constructor = Mock()
    monkeypatch.setattr("src.application.trace.NTraceClient", constructor)
    monkeypatch.setenv("NTRACE_ENABLED", "true")
    monkeypatch.setenv("NTRACE_SERVER_URL", "https://wrong.example.test")
    first = trace_client()
    assert trace_client() is first
    constructor.assert_called_once_with(
        enabled=False, server_url="https://trace.example.test", request_timeout=8.0
    )


def test_codex_auth_path_uses_snapshot(configuration_service, monkeypatch, tmp_path):
    from src.codex.codex_oauth import CodexOAuthTokenProvider

    configuration_service._snapshot = parse_configuration(
        {"schema_version": 1, "other": {"codex": {"auth_json": str(tmp_path / "saved.json")}}}
    )
    monkeypatch.setenv("AUTH_JSON", str(tmp_path / "changed.json"))
    assert CodexOAuthTokenProvider().auth_path == tmp_path / "saved.json"


def test_training_run_on_worker_only_reads_initialized_service(configuration_service, monkeypatch):
    from langchain_core.messages import AIMessage

    from src.configuration.manager import ConfigurationManager
    from tests.engine_helpers import Model
    from trainer.raw_run_adapter import RawRunSlave
    from trainer.tests.test_raw_run_adapter import ReportEngine

    initializer = Mock(side_effect=AssertionError("worker must not initialize"))
    monkeypatch.setattr(ConfigurationManager, "initialize", initializer)
    # Assemble the client on the main thread, as the production master does.
    trace_client()
    slave = RawRunSlave(
        engine_factory=lambda **kw: ReportEngine(**kw),
        model_factory=lambda: Model(replies=[AIMessage(content="complete")]),
        print_result=False,
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(slave.run).result(timeout=5).output == "complete"
    initializer.assert_not_called()


def test_legacy_interpolation_uses_captured_environment(tmp_path, monkeypatch):
    monkeypatch.setattr("src.configuration.loader.WORKSPACE_ROOT", tmp_path)
    monkeypatch.setattr(os, "environ", {"PREFIX": "environment"})
    (tmp_path / ".env").write_text(
        "PREFIX=file\nFEISHU_APP_ID=${PREFIX}-app\nFEISHU_APP_SECRET=${MISSING:-fallback}\n"
    )
    config = load_configuration()
    assert config.channels["feishu"]["default"]["app_id"] == "environment-app"
    assert config.channels["feishu"]["default"]["app_secret"] == "fallback"
    assert os.environ == {"PREFIX": "environment"}
