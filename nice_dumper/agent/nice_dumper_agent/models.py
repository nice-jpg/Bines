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


@dataclass(frozen=True)
class OptimizationRound:
    index: int
    xml0_length: int
    xml1_length: int
    l0_count: int
    l1_count: int
    score: ScoreResult
    suggestion: str


@dataclass(frozen=True)
class OptimizerRunResult:
    optimizer_path: str
    best_score: float
    rounds: list[OptimizationRound]
