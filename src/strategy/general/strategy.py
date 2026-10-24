"""General-page routing configuration without page-state ownership."""

from src.agent_runtime.strategy.base import Strategy
from src.conductor.state import ConductorState


class GeneralStrategy(Strategy[ConductorState]):
    pass
