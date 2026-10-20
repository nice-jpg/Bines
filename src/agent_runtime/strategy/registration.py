"""Immutable construction contract for one strategy and its state namespace."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from langchain.agents.middleware.types import AgentState

from .base import Strategy


@dataclass(frozen=True)
class StrategyRegistration[StateT: AgentState]:
    name: str
    strategy: Strategy[StateT]
    state_schema: type
    initial_state: Callable[[], Mapping[str, object]]
