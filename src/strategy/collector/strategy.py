"""Collector hook routing without assignment validation logic."""

from langchain.agents.middleware.types import AgentState

from src.agent_runtime.strategy.base import Strategy
from src.strategy.collector.assignment import CollectorAssignmentMiddleware

from .state import CollectorState


class CollectorStrategy[StateT: AgentState](Strategy[StateT]):
    def __init__(self, assignment: CollectorState) -> None:
        super().__init__(constraints=[CollectorAssignmentMiddleware(assignment)], features=[])
