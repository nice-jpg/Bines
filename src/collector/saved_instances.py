"""Thread-safe storage for collector evidence grouped by canonical page path."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from threading import RLock
from typing import Any


@dataclass
class CollectorSavedInstance:
    """Evidence captured during one concrete visit to a canonical page."""

    page_path: str
    page_visit_id: str
    cases: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "page_path": self.page_path,
            "page_visit_id": self.page_visit_id,
            "cases": deepcopy(self.cases),
        }


class CollectorSavedInstanceMap:
    """Map each canonical path to all page visits and their saved training cases."""

    def __init__(self) -> None:
        self._instances_by_path: dict[str, list[CollectorSavedInstance]] = {}
        self._revisions_by_path: dict[str, int] = {}
        self._lock = RLock()

    def register(self, page_path: str, page_visit_id: str) -> None:
        path = _require_identifier(page_path, "page_path")
        visit_id = _require_identifier(page_visit_id, "page_visit_id")
        with self._lock:
            instances = self._instances_by_path.setdefault(path, [])
            if any(instance.page_visit_id == visit_id for instance in instances):
                return
            instances.append(CollectorSavedInstance(path, visit_id))
            self._revisions_by_path.setdefault(path, 0)

    def save_case(
        self,
        page_path: str,
        page_visit_id: str,
        case: dict[str, Any],
    ) -> None:
        path = _require_identifier(page_path, "page_path")
        visit_id = _require_identifier(page_visit_id, "page_visit_id")
        case_id = _require_identifier(case.get("case_id"), "case.case_id")
        with self._lock:
            self.register(path, visit_id)
            instance = next(
                item
                for item in self._instances_by_path[path]
                if item.page_visit_id == visit_id
            )
            saved = deepcopy(case)
            for index, existing in enumerate(instance.cases):
                if existing.get("case_id") == case_id:
                    instance.cases[index] = saved
                    break
            else:
                instance.cases.append(saved)
            self._revisions_by_path[path] = self._revisions_by_path.get(path, 0) + 1

    def cases_for_path(self, page_path: str) -> list[dict[str, Any]]:
        path = _require_identifier(page_path, "page_path")
        with self._lock:
            return [
                deepcopy(case)
                for instance in self._instances_by_path.get(path, [])
                for case in instance.cases
            ]

    def instances_for_path(self, page_path: str) -> list[dict[str, Any]]:
        path = _require_identifier(page_path, "page_path")
        with self._lock:
            return [
                instance.snapshot()
                for instance in self._instances_by_path.get(path, [])
            ]

    def revision_for_path(self, page_path: str) -> int:
        path = _require_identifier(page_path, "page_path")
        with self._lock:
            return self._revisions_by_path.get(path, 0)


def _require_identifier(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty string.")
    return normalized
