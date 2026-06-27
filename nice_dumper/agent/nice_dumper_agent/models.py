"""Data models used by the nice_dumper optimizer agent."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FunctionRegion:
    bounds: str
    label: str


@dataclass(frozen=True)
class RecognizerResult:
    functions: list[FunctionRegion]
    raw_output: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class MatchResult:
    source: FunctionRegion
    target: FunctionRegion | None
    iou: float


@dataclass(frozen=True)
class ScoreResult:
    score: float
    fidelity: float
    compression: float
    missing_count: int
    missing_penalty: float
    matches: list[MatchResult] = field(default_factory=list)
    execution_error: str | None = None
    hidden_pruning: float = 0.0
    hidden_pruning_reward: float = 0.0
    hidden_subtree_count: int = 0
    hidden_candidate_count: int = 0
    hidden_removed_count: int = 0


@dataclass(frozen=True)
class OptimizationRound:
    index: int
    xml0_length: int
    xml1_length: int
    l0_count: int
    l1_count: int
    score: ScoreResult
    reason: str
    suggestion: str


@dataclass(frozen=True)
class OptimizerProposal:
    reason: str
    source: str


@dataclass(frozen=True)
class OptimizerRunResult:
    optimizer_path: str
    best_score: float
    rounds: list[OptimizationRound]
