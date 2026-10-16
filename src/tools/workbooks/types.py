"""Workbook persistence results."""

from typing import NotRequired, TypedDict


class PersistenceResult(TypedDict):
    ok: bool
    skipped: NotRequired[bool]
    reason: NotRequired[str]
    error_type: NotRequired[str]
    error: NotRequired[str]
    path: NotRequired[str]
    merchant: NotRequired[str]
    index_sheet: NotRequired[str]
    index_row: NotRequired[int]
    merchant_sheet: NotRequired[str]
    appended_items: NotRequired[int]
    skipped_items: NotRequired[int]
