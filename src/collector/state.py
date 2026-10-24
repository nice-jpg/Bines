"""Collector graph schema, independent of Conductor page strategies."""

from src.agent_runtime.strategy.state import StrategyState
from src.strategy.collector.state import CollectorState


class CollectorAgentState(StrategyState):
    collector: CollectorState
