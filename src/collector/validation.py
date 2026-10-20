"""Types and state for a collector's visual validation session."""

from dataclasses import dataclass, field
from typing import NotRequired, TypedDict

from src.collector.evaluation import EvaluationGrade
from src.collector.types import ValidationStamp
from src.common.utils.json import JsonObject
from src.device import PageBridge, PageSnapshot


class BaselineRevision(TypedDict):
    baseline: JsonObject
    evidence: str
    revised_at: str


class ValidationCase(TypedDict):
    case_id: str
    page_visit_id: str
    run_id: str
    snapshot: PageSnapshot
    baseline: JsonObject | None
    image: str | None
    schema: JsonObject
    revision: int
    random_state: bool
    guide_fingerprint: str
    format_version: NotRequired[int]
    guide: NotRequired[str]
    screenshot_at: NotRequired[str]
    baseline_revisions: NotRequired[list[BaselineRevision]]


@dataclass
class CollectorValidationSession:
    """Own evidence, active snapshot and regression invalidation for one visit."""

    cases: dict[str, ValidationCase]
    historical_ids: set[str]
    active_id: str | None = None
    frozen: PageBridge | None = None
    marked: set[str] = field(default_factory=set[str])
    reviewed_images: set[str] = field(default_factory=set[str])
    grades: dict[str, EvaluationGrade] = field(default_factory=dict[str, EvaluationGrade])
    tested_stamp: ValidationStamp | None = None

    def invalidate(self) -> None:
        self.tested_stamp = None
        self.grades.clear()

    def return_to_live(self) -> None:
        self.active_id = None
        self.frozen = None
        self.marked.clear()
