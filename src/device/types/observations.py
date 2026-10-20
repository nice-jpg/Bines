"""Collection observations shared by device and business services."""

from dataclasses import dataclass
from enum import StrEnum

from src.common.utils.json import JsonValue
from src.device.types.page import PageSnapshot


class CollectionSwipe(StrEnum):
    LONG = "swipe_up_long"
    SHORT = "swipe_up"


@dataclass(frozen=True)
class SwipeObservation:
    """One device operation and its synchronized full snapshot."""

    hint: JsonValue
    snapshot: PageSnapshot | None
    change_ratio: float | None
    error: str = ""
