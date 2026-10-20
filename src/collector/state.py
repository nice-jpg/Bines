"""Collector graph schema, independent of Conductor page strategies."""

from langchain.agents.middleware import AgentState

from src.strategy.collector.state import CollectorState


class CollectorAgentState(AgentState):
    collector: CollectorState
