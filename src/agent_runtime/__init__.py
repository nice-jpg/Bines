"""Stable agent execution independent of device and collection business logic."""

from .runtime import AgentRunResult, AgentRuntime
from .persistence import AgentPersistenceLayer, AgentResponse
from .communication import RuntimeCommunication, tool_yield_reason


def create_runtime(*, name: str, model, tools, system_prompt: str, middleware=()) -> AgentRuntime:
    """Build a checkpointed agent with automatic tracing and failure handling."""
    return AgentRuntime(name=name, model=model, tools=tools, system_prompt=system_prompt, middleware=middleware)


__all__ = ["create_runtime", "AgentRuntime", "AgentRunResult", "AgentPersistenceLayer", "AgentResponse", "RuntimeCommunication", "tool_yield_reason"]
