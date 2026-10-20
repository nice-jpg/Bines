"""Collector training, preparation and restoration results."""

from typing import Literal, NotRequired, TypedDict

from src.common.utils.json import JsonObject


class NormalSwipeProfile(TypedDict):
    change_threshold: float
    sample_count: int
    actions: list[str]
    warning: str


class TrainingResult(TypedDict):
    ready: bool
    score: int
    validated_states: int
    script_fingerprint: str
    normal_swipe_profile: NormalSwipeProfile | None


class RestorationResult(TypedDict):
    restored: bool
    strategy: Literal["reset_tap"]
    tap_attempted: bool


type ValidationStamp = tuple[str, str, str, str | None]


class CollectorPreparationResult(TypedDict):
    ok: bool
    page_path: str
    page_visit_id: str
    collector_message: str
    merchant_profile: JsonObject
    trained: bool
    verification: TrainingResult
    restoration: RestorationResult
    normal_swipe_profile: NormalSwipeProfile | None
    error_type: NotRequired[str]
    error: NotRequired[str]
