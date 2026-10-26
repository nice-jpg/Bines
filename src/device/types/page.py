"""Portable page evidence shared by device, training and collection."""

from typing import Literal, TypedDict

from pydantic import TypeAdapter

from src.common.utils.validation import is_string_mapping


class PageSnapshot(TypedDict):
    xml: str
    aliases: dict[str, list[int]]
    snapshot_id: str
    captured_at: str


class PageArtifact(TypedDict):
    page_xml: str
    page_snapshot: PageSnapshot


class ChangedRegion(TypedDict):
    id: int
    node_update_ratio: float
    view_update_ratio: float


class PageChangeHint(TypedDict):
    kind: Literal["page_change_hint"]
    node_update_ratio: float
    view_update_ratio: float
    regions: list[ChangedRegion]


_SNAPSHOT_ADAPTER: TypeAdapter[PageSnapshot] = TypeAdapter(PageSnapshot)


def artifact_snapshot(value: object) -> PageSnapshot | None:
    """Validate page evidence at the framework artifact boundary."""
    if not is_string_mapping(value):
        return None
    snapshot: object = value.get("page_snapshot")
    if snapshot is None:
        return None
    return _SNAPSHOT_ADAPTER.validate_python(snapshot, strict=True)
