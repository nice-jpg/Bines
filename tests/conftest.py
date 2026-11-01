"""Release all test gateways without replacing the singleton service."""

import pytest


@pytest.fixture(autouse=True)
def release_gateway():
    yield
    from gateway_helpers import HANDLES
    for handle in HANDLES:
        handle.request_stop()
    HANDLES.clear()


@pytest.fixture(autouse=True)
def configuration_service(monkeypatch):
    """Integration tests get a frozen service; lifecycle tests use fresh subprocesses."""
    from src.configuration.loader import parse_configuration
    from src.configuration.manager import ConfigurationManager
    from src.common.tracing import trace_client

    service = object.__new__(ConfigurationManager)
    service._snapshot = parse_configuration({"schema_version": 1, "trace": {"enabled": False}})
    monkeypatch.setattr(ConfigurationManager, "_instance", service)
    trace_client.cache_clear()
    yield service
    if trace_client.cache_info().currsize:
        trace_client().close()
    trace_client.cache_clear()
