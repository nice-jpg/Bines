"""Page identities and arrival evidence, independent of merchant progress."""

from typing import Final

from typing_extensions import TypedDict

GENERAL: Final = "general"


class PageVisit(TypedDict):
    visit_id: str
    path: str
    strategy: str


class PendingVisit(TypedDict):
    candidate_id: str
    path: str
    strategy: str
    app_root: str
    package_name: str
    merchant_name: str
    observation_id: str
    rejected: bool


class GeneralState(TypedDict, total=False):
    active: bool
    app_root: str
    visit: PageVisit | None
    pending: PendingVisit | None


def initial_general_state() -> GeneralState:
    return {"active": False, "app_root": "", "visit": None, "pending": None}
