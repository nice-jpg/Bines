"""Collection execution completion and admission results."""

from typing import NotRequired, TypedDict

from src.tools.workbooks.types import PersistenceResult


class CompletionPayload(TypedDict):
    ok: bool
    done: bool
    initialized: bool
    latest_new_count: int
    consecutive_empty_swipes: int
    page_path: str
    page_visit_id: str
    error_type: NotRequired[str]


class IncompleteRow(TypedDict):
    index: int
    missing: list[str]


class AdmissionAudit(TypedDict):
    incomplete: list[IncompleteRow]
    persistence: PersistenceResult
