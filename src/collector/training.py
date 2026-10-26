"""Visit-scoped collector training evidence with synchronized updates."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from threading import Lock

from src.collector.types import RestorationResult
from src.common.utils.json import JsonObject, JsonValue
from src.page.paths import normalize_page_path


@dataclass
class CollectorTrainingRecord:
    """Training and archival state for one concrete page visit."""

    visit_id: str
    page_path: str
    trained: bool = False
    merchant_profile: JsonObject = field(default_factory=lambda: {})
    script_fingerprint: str = ""
    restoration: RestorationResult | None = None
    contract_fingerprint: str = ""
    change_threshold: float | None = None
    normal_swipe_samples: int = 0


class CollectorTrainingRegistry:
    """Track collector results by concrete page visit, never by page class."""

    def __init__(self) -> None:
        self._records: dict[str, CollectorTrainingRecord] = {}
        self._lock: Lock = Lock()

    def register_visit(self, visit_id: str, current_path: str) -> CollectorTrainingRecord:
        visit_id = str(visit_id or "").strip()
        canonical_path: str = self._canonical_path(current_path)
        if not visit_id:
            raise ValueError("visit_id is required.")
        if not canonical_path:
            raise ValueError("current_path is required.")
        with self._lock:
            record: CollectorTrainingRecord | None = self._records.get(visit_id)
            if record is not None and record.page_path != canonical_path:
                raise ValueError("visit_id is already registered for another page path.")
            if record is None:
                record = CollectorTrainingRecord(visit_id=visit_id, page_path=canonical_path)
                self._records[visit_id] = record
            return _copy_training_record(record)

    def mark_trained(
        self,
        visit_id: str,
        current_path: str,
        *,
        merchant_profile: Mapping[str, JsonValue] | None = None,
        script_fingerprint: str = "",
        restoration: RestorationResult | None = None,
        contract_fingerprint: str = "",
    ) -> None:
        self.register_visit(visit_id, current_path)
        with self._lock:
            record: CollectorTrainingRecord = self._records[visit_id]
            record.trained = True
            record.change_threshold = None
            record.normal_swipe_samples = 0
            record.merchant_profile = dict(merchant_profile or {})
            record.script_fingerprint = str(script_fingerprint or "")
            record.restoration = restoration.copy() if restoration is not None else None
            record.contract_fingerprint = contract_fingerprint

    def mark_untrained(
        self,
        visit_id: str,
        current_path: str,
        *,
        merchant_profile: Mapping[str, JsonValue] | None = None,
        script_fingerprint: str = "",
        restoration: RestorationResult | None = None,
    ) -> None:
        self.register_visit(visit_id, current_path)
        with self._lock:
            record: CollectorTrainingRecord = self._records[visit_id]
            record.trained = False
            record.change_threshold = None
            record.normal_swipe_samples = 0
            record.merchant_profile = dict(merchant_profile or {})
            record.script_fingerprint = str(script_fingerprint or "")
            record.restoration = restoration.copy() if restoration is not None else None

    def is_trained(self, visit_id: str, current_path: str | None = None) -> bool:
        record: CollectorTrainingRecord | None = self.record_for(visit_id)
        if record is None or not record.trained:
            return False
        return current_path is None or record.page_path == self._canonical_path(current_path)

    def set_swipe_threshold(self, visit_id: str, threshold: float, sample_count: int) -> None:
        """Attach a verified whole-page view-change threshold to a trained visit."""
        with self._lock:
            record: CollectorTrainingRecord = self._records[visit_id]
            if not record.trained or not 0 <= threshold <= 1 or sample_count < 2:
                raise ValueError("A trained visit and verified swipe samples are required")
            record.change_threshold = threshold
            record.normal_swipe_samples = sample_count

    def record_for(self, visit_id: str) -> CollectorTrainingRecord | None:
        with self._lock:
            record: CollectorTrainingRecord | None = self._records.get(str(visit_id or ""))
            return _copy_training_record(record) if record is not None else None

    def _canonical_path(self, current_path: str) -> str:
        parts, error = normalize_page_path(current_path)
        if error or not parts:
            return ""
        return "/".join(parts)


def _copy_training_record(record: CollectorTrainingRecord) -> CollectorTrainingRecord:
    return CollectorTrainingRecord(
        visit_id=record.visit_id,
        page_path=record.page_path,
        trained=record.trained,
        merchant_profile=dict(record.merchant_profile),
        script_fingerprint=record.script_fingerprint,
        restoration=record.restoration.copy() if record.restoration is not None else None,
        contract_fingerprint=record.contract_fingerprint,
        change_threshold=record.change_threshold,
        normal_swipe_samples=record.normal_swipe_samples,
    )
