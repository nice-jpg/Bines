"""Pure target resolution and candidate-bound page confirmation."""

import json
from dataclasses import dataclass
from typing import Final

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage

from Reco.model_interface import TOOLS as PAGE_TOOLS
from src.common.utils.json import JsonValue
from src.page.mechanism import PageMechanism
from src.page.paths import normalize_page_path
from src.strategy.general.declarations import read_page_strategy
from src.strategy.general.state import PageArrival

OBSERVATION_TOOLS: Final[frozenset[str]] = frozenset({tool["name"] for tool in PAGE_TOOLS} | {"screenshot"})
NAVIGATION_TOOLS: Final[frozenset[str]] = frozenset({"run_package", "navigate_to", "navigate_back"})
MERCHANT_TOOLS: Final[frozenset[str]] = frozenset({"prepare_collector", "collect_continuously", "is_done"})
PAGE_OPERATIONS: Final[frozenset[str]] = (
    OBSERVATION_TOOLS
    | NAVIGATION_TOOLS
    | MERCHANT_TOOLS
    | frozenset(
        {
            "close_package",
            "confirm_page",
            "tap_in_page",
            "swipe_up",
            "swipe_up_long",
            "swipe_down",
            "update_page_notes",
        }
    )
)


@dataclass(frozen=True)
class PageTarget:
    path: str
    strategy: str
    app_root: str


def text_argument(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def reject(request: ToolCallRequest, reason: str, detail: str) -> ToolMessage:
    return ToolMessage(
        content=json.dumps({"ok": False, "error_type": reason, "detail": detail}, ensure_ascii=False),
        tool_call_id=request.tool_call["id"],
        name=request.tool_call["name"],
        status="error",
    )


def resolve_page_target(mechanism: PageMechanism, strategy_names: frozenset[str], path: str) -> PageTarget:
    parts, error = normalize_page_path(path)
    if error or not parts:
        raise ValueError("Navigation requires an absolute canonical page path.")
    canonical: str = "/".join(parts)
    mechanism.validate_page_manual(canonical)
    strategy: str = read_page_strategy(mechanism, canonical)
    if strategy not in strategy_names:
        raise ValueError(f"Unregistered page strategy: {strategy}")
    return PageTarget(canonical, strategy, parts[0])


def validate_arrival(arrival: PageArrival | None, candidate_id: str) -> None:
    if (
        arrival is None
        or arrival["status"] == "confirmed"
        or arrival["candidate_id"] != candidate_id
    ):
        raise ValueError("Invalid or stale page candidate.")
