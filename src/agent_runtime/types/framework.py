"""Generic types needed when LangChain combines heterogeneous middleware schemas."""

from typing import Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
)
from langchain_core.messages import AnyMessage
from langgraph.graph.state import CompiledStateGraph  # pyright: ignore[reportMissingTypeStubs]
from typing_extensions import TypedDict

from src.agent_messages.types import GraphInput
from src.serialization.json import JsonObject

# LangChain combines distinct state/context schemas dynamically. Its invariant
# generics cannot describe that heterogeneous sequence with a common state type.
type RuntimeMiddleware = AgentMiddleware[Any, Any, Any]


class GraphOutput(TypedDict):
    messages: list[AnyMessage]
    structured_response: NotRequired[JsonObject]


type RuntimeGraph = CompiledStateGraph[AgentState[JsonObject], None, GraphInput, GraphOutput]
