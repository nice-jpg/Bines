"""Assemble production tools without constructing a model graph or touching devices."""
from src.conductor.engine import ConductorEngine


def conductor_tools():
    engine = object.__new__(ConductorEngine)
    engine._model = "test-model"
    from gateway_helpers import GatewayHandle, RecordingChannel
    engine.handle = GatewayHandle(RecordingChannel()).handle
    engine._notifier = lambda message: None
    from src.agent_runtime.messaging.tools import communication_tools
    return [*engine._create_tools(), *communication_tools(engine)]
