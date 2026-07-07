"""Public API for the cognitive-control agent."""

from .contracts import CognitiveSlave, SlaveDebugInfo, SlaveDebugInfoProvider
from .main_agent import MasterConfig, build_master_agent, run_master
from .models import CognitiveRound, Evaluation, MasterRunResult, PromptChange

__all__ = [
    "CognitiveRound",
    "CognitiveSlave",
    "Evaluation",
    "MasterConfig",
    "MasterRunResult",
    "PromptChange",
    "SlaveDebugInfo",
    "SlaveDebugInfoProvider",
    "build_master_agent",
    "run_master",
]
