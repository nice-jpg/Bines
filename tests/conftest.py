"""Release all test gateways without replacing the singleton service."""

import pytest
from src.application.gateway_manager import GatewayManager


@pytest.fixture(autouse=True)
def release_gateway():
    manager = GatewayManager.instance()
    yield
    for gateway_id in list(manager._registrations):
        manager.stop(gateway_id)
        manager.finish_output(gateway_id)
        manager.join(gateway_id)
        manager.release(gateway_id)


@pytest.fixture(autouse=True)
def configuration_service(monkeypatch):
    """Integration tests get a frozen service; lifecycle tests use fresh subprocesses."""
    from src.configuration.loader import parse_configuration
    from src.configuration.manager import ConfigurationManager
    from src.application.trace import trace_client

    service = object.__new__(ConfigurationManager)
    service._snapshot = parse_configuration({"schema_version": 1, "trace": {"enabled": False}})
    monkeypatch.setattr(ConfigurationManager, "_instance", service)
    trace_client.cache_clear()
    yield service
    if trace_client.cache_info().currsize:
        trace_client().close()
    trace_client.cache_clear()
