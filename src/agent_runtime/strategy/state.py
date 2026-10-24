"""Fixed routing field shared by strategy-enabled graphs and their inputs."""

from langchain.agents.middleware.types import AgentState

from src.agent_messages.types import GraphInput


class StrategyState(AgentState):
    active_strategy: str


class StrategyInput(GraphInput):
    active_strategy: str
