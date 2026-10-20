"""Entrypoint assembles business engines without constructing their tools."""

from unittest.mock import Mock
import pytest
from src import run_agent
from src.application.gateway_manager import GatewayManager


def test_main_joins_worker_before_gateway_release_and_model_close(monkeypatch):
    events = []
    gateway = Mock()
    gateway.create.return_value = "id"
    worker = Mock()
    worker.join.side_effect = [KeyboardInterrupt(), None]
    worker.join.return_value = None
    gateway.join.side_effect = lambda *a: events.append("gateway")
    model = object()
    engine = Mock(gateway_id="id")
    build_engine = Mock(return_value=engine)
    monkeypatch.setattr(run_agent, "build_model", lambda: model)
    monkeypatch.setattr(run_agent, "ConductorEngine", build_engine)
    monkeypatch.setattr(run_agent, "AgentCoordinator", lambda e: "coordinator")
    monkeypatch.setattr(run_agent, "AgentWorker", lambda c: worker)
    monkeypatch.setattr(run_agent, "FeishuChannel", lambda *a: "channel")
    monkeypatch.setattr(run_agent, "load_feishu_config", lambda: "config")
    monkeypatch.setattr(GatewayManager, "instance", lambda: gateway)
    gateway.release.side_effect = lambda *a: events.append("release")
    monkeypatch.setattr(run_agent, "close_model", lambda m: events.append("model"))
    monkeypatch.setattr("sys.argv", ["run_agent", "--max-iterations", "42"])
    run_agent.main()
    build_engine.assert_called_once_with(model=model, gateway_id="id", max_iterations=42)
    assert events == ["gateway", "model", "release"]
    assert worker.join.call_count == 2
    gateway.stop.assert_called_once_with("id")


def test_failed_engine_construction_releases_gateway_and_model(monkeypatch):
    monkeypatch.setattr(run_agent, "load_feishu_config", lambda: None)
    monkeypatch.setattr(run_agent, "FeishuChannel", lambda *a: Mock())
    monkeypatch.setattr(run_agent, "build_model", lambda: "model")

    def fail(**kwargs):
        raise ValueError("invalid engine")

    monkeypatch.setattr(run_agent, "ConductorEngine", fail)
    close = Mock()
    monkeypatch.setattr(run_agent, "close_model", close)
    monkeypatch.setattr("sys.argv", ["run_agent"])
    with pytest.raises(ValueError, match="invalid engine"):
        run_agent.main()
    assert not GatewayManager.instance()._registrations
    close.assert_called_once_with("model")
