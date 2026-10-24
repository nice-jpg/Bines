"""Page identities and pending arrivals, independent of merchant progress."""

from typing import Final, Literal

from typing_extensions import TypedDict

GENERAL: Final = "general"


class PageVisit(TypedDict):
    visit_id: str
    path: str
    strategy: str


class PageArrival(TypedDict):
    candidate_id: str
    path: str
    strategy: str
    app_root: str
    status: Literal["awaiting_confirmation", "confirmed"]


class GeneralState(TypedDict, total=False):
    active: bool
    app_root: str
    visit: PageVisit | None
    arrival: PageArrival | None


def initial_general_state() -> GeneralState:
    return {"active": False, "app_root": "", "visit": None, "arrival": None}


def unconfirmed_arrival(state: GeneralState) -> PageArrival | None:
    arrival: PageArrival | None = state.get("arrival")
    return arrival if arrival is not None and arrival["status"] != "confirmed" else None
