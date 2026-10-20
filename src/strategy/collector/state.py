"""Checkpoint identity of one page-local training assignment."""

from typing import Final

from typing_extensions import TypedDict

COLLECTOR: Final[str] = "collector"


class CollectorState(TypedDict):
    page_path: str
    page_visit_id: str
    run_id: str
