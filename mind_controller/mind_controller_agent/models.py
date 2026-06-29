"""Data models for cognitive-control runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Evaluation:
    total: int
    dimensions: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CognitiveRound:
    index: int
    run_ref: str
    score_ref: str
    score: Evaluation
    prompt_revision: int
    prompt_hashes: dict[str, str]


@dataclass(frozen=True)
class PromptChange:
    revision: int
    path: str
    reason: str
    before_hash: str
    after_hash: str


@dataclass(frozen=True)
class MasterRunResult:
    best_score: int
    best_round: int
    rounds: list[CognitiveRound]
    changes: list[PromptChange]
    restored_best_prompts: bool

