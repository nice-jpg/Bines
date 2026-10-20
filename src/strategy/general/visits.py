"""Atomic page operation admission and evidence-bound arrival confirmation."""

import hashlib
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from threading import Lock
from typing import Final
from uuid import uuid4

from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from Reco.model_interface import TOOLS as PAGE_TOOLS
from src.agent_messages.tool_results import ToolResult
from src.common.utils.json import JsonValue
from src.conductor.state import (
    ConductorState,
    ConductorUpdate,
    combine_updates,
    complete_update,
    current_visit,
    merchant_identity,
)
from src.middleware.tool_io import (
    ToolInvocation,
    command_update,
    tool_succeeded,
    with_state_update,
    with_warning,
)
from src.page.mechanism import PageMechanism
from src.page.paths import normalize_page_path
from src.strategy.general.declarations import read_page_strategy
from src.strategy.general.state import PageVisit, PendingVisit
from src.strategy.merchant.state import MERCHANT, MERCHANT_COMPLETED, MerchantState, is_merchant_visit

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


def text_argument(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def reject(request: ToolCallRequest, reason: str, detail: str) -> ToolMessage:
    return ToolMessage(
        content=json.dumps({"ok": False, "error_type": reason, "detail": detail}, ensure_ascii=False),
        tool_call_id=request.tool_call["id"],
        name=request.tool_call["name"],
        status="error",
    )


def initial_page_context_state() -> ConductorUpdate:
    return {"general": {"active": True, "app_root": "", "visit": None, "pending": None}}


class PageVisitController:
    def __init__(
        self,
        mechanism: PageMechanism,
        *,
        strategy_names: frozenset[str],
        begin_merchant: Callable[[PageVisit], MerchantState],
        end_merchant: Callable[[], MerchantState],
    ) -> None:
        self.mechanism: PageMechanism = mechanism
        self._strategy_names: frozenset[str] = strategy_names
        self._begin_merchant: Callable[[PageVisit], MerchantState] = begin_merchant
        self._end_merchant: Callable[[], MerchantState] = end_merchant
        self._claims: OrderedDict[str, str] = OrderedDict()
        self._claim_lock: Lock = Lock()
        self._operation_lock: Lock = Lock()

    def strategy_kind(self, path: str) -> str:
        name: str = read_page_strategy(self.mechanism, path)
        if name not in self._strategy_names:
            raise ValueError(f"Unregistered page strategy: {name}")
        if name == MERCHANT and self.mechanism.collection_contract(path) is None:
            raise ValueError(f"Merchant page has no collection contract: {path}")
        return name

    def validate_targets(self, paths: tuple[str, ...]) -> None:
        for path in paths:
            parts: list[str] = path.split("/")
            for length in range(1, len(parts) + 1):
                self.strategy_kind("/".join(parts[:length]))

    def _validate(self, request: ToolCallRequest) -> ToolMessage | None:
        call: ToolInvocation = ToolInvocation.from_request(request)
        state: ConductorState = call.state
        pending: PendingVisit | None = state["general"].get("pending")
        if pending is not None:
            allowed: bool = call.name in OBSERVATION_TOOLS | NAVIGATION_TOOLS | {
                "confirm_page",
                "think",
                "talk",
                "authenticate_captcha",
                "captcha_authenticated",
            } or (call.name == "tap_in_page" and call.args.get("purpose") == "dismiss_overlay")
            if not allowed:
                return reject(
                    request,
                    "page_confirmation_required",
                    "Observe the candidate page and confirm_page before other operations.",
                )
        elif call.name == "confirm_page":
            return reject(request, "no_candidate_page", "There is no pending navigation to confirm.")
        if (
            not current_visit(state).get("path")
            and pending is None
            and call.name in PAGE_OPERATIONS - {"run_package"}
        ):
            return reject(request, "application_not_open", "Open the configured application first.")
        if call.name in {"run_package", "close_package"}:
            package: str = text_argument(call.args.get("package_name"))
            expected: str = state["probe"].get("package_name", "")
            if expected and package != expected:
                return reject(
                    request, "wrong_application", "The operation must target this probe's configured package."
                )
        if call.name in NAVIGATION_TOOLS:
            try:
                self.begin_navigation(call)
            except (ValueError, FileNotFoundError) as exc:
                return reject(request, "invalid_navigation_target", str(exc))
        return None

    def begin_navigation(self, call: ToolInvocation) -> PendingVisit:
        state: ConductorState = call.state
        previous: PendingVisit | None = state["general"].get("pending")
        root: str = previous["app_root"] if previous else state["general"].get("app_root", "")
        package: str = state["probe"].get("package_name", "")
        if call.name == "run_package":
            package = text_argument(call.args.get("package_name"))
            root = self.mechanism.home_path_for_package(package)
            path: str = root
        else:
            parts, error = normalize_page_path(text_argument(call.args.get("next_path")))
            path = "/".join(parts)
            if error or not root or path.split("/")[0] != root:
                raise ValueError("Navigation requires a canonical path in the current application.")
            if previous is None and path == current_visit(state).get("path"):
                raise ValueError("Use tap_in_page for same-page operations.")
        self.mechanism.validate_page_manual(path)
        kind: str = self.strategy_kind(path)
        merchant: str = merchant_identity(text_argument(call.args.get("merchant_name")))
        if call.name == "navigate_to" and kind == MERCHANT:
            if not merchant:
                raise ValueError("A full merchant_name is required for this structured page.")
            if merchant in state["probe"].get("completed_merchants", {}).get(path, ()):
                raise ValueError("merchant_already_collected: choose a different visible merchant.")
        return PendingVisit(
            candidate_id=uuid4().hex,
            path=path,
            strategy=kind,
            app_root=root,
            package_name=package,
            merchant_name=merchant,
            observation_id="",
            rejected=False,
        )

    def _claim(self, request: ToolCallRequest) -> ToolMessage | None:
        call: ToolInvocation = ToolInvocation.from_request(request)
        if call.name not in PAGE_OPERATIONS:
            return None
        latest: AIMessage | None = next(
            (m for m in reversed(call.state["messages"]) if isinstance(m, AIMessage)), None
        )
        if latest is None:
            return None
        first: str | None = next((c["id"] for c in latest.tool_calls if c["name"] in PAGE_OPERATIONS), None)
        if first is not None and first != call.call_id:
            return reject(
                request,
                "multiple_page_operations",
                "Only the first page operation in a model response may run, including training and observation.",
            )
        identity: str = (
            latest.id or hashlib.sha256(json.dumps(latest.tool_calls, sort_keys=True).encode()).hexdigest()
        )
        key: str = f"{call.config.get('configurable', {}).get('thread_id', 'default')}:{identity}"
        with self._claim_lock:
            if key in self._claims:
                return reject(
                    request, "multiple_page_operations", "This model step already claimed a page operation."
                )
            self._claims[key] = call.call_id
            while len(self._claims) > 2048:
                self._claims.popitem(last=False)
        return None

    def execute_operation(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], ToolResult]
    ) -> ToolResult:
        rejection: ToolMessage | None = self._validate(request) or self._claim(request)
        if rejection is not None:
            return rejection
        if request.tool_call["name"] not in PAGE_OPERATIONS:
            return self._complete(request, handler(request))
        if not self._operation_lock.acquire(blocking=False):
            return reject(request, "page_operation_busy", "Another complete page operation is running.")
        try:
            candidate: PendingVisit | None = (
                self.begin_navigation(ToolInvocation.from_request(request))
                if request.tool_call["name"] in NAVIGATION_TOOLS
                else None
            )
            return self._complete(request, self._after(request, handler(request), candidate))
        finally:
            self._operation_lock.release()

    async def aexecute_operation(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolResult]]
    ) -> ToolResult:
        rejection: ToolMessage | None = self._validate(request) or self._claim(request)
        if rejection is not None:
            return rejection
        if request.tool_call["name"] not in PAGE_OPERATIONS:
            return self._complete(request, await handler(request))
        if not self._operation_lock.acquire(blocking=False):
            return reject(request, "page_operation_busy", "Another complete page operation is running.")
        try:
            candidate: PendingVisit | None = (
                self.begin_navigation(ToolInvocation.from_request(request))
                if request.tool_call["name"] in NAVIGATION_TOOLS
                else None
            )
            return self._complete(request, self._after(request, await handler(request), candidate))
        finally:
            self._operation_lock.release()

    def _complete(self, request: ToolCallRequest, result: ToolResult) -> ToolResult:
        if isinstance(result, Command):
            return with_state_update(
                result, complete_update(ToolInvocation.from_request(request).state, command_update(result))
            )
        return result

    def _after(
        self, request: ToolCallRequest, result: ToolResult, candidate: PendingVisit | None
    ) -> ToolResult:
        if not tool_succeeded(result):
            return result
        call: ToolInvocation = ToolInvocation.from_request(request)
        if candidate is not None:
            update: ConductorUpdate = {
                "general": {"pending": candidate},
                # Keep the last confirmed visit only as navigation history while pending.
                # Its collection evidence must not describe the new device page.
                "merchant": self._end_merchant(),
                "probe": {"package_name": candidate["package_name"], "current_merchant": ""},
            }
            visit: PageVisit | None = call.state["general"].get("visit")
            if (
                visit is not None
                and is_merchant_visit(visit)
                and (
                    call.state["merchant"].get("phase") != MERCHANT_COMPLETED
                    or bool(call.args.get("recovery_reason"))
                )
                and call.state["general"].get("pending") is None
            ):
                unfinished: dict[str, dict[str, str]] = dict(
                    call.state["probe"].get("unfinished_merchants", {})
                )
                unfinished[current_visit(call.state).get("visit_id", "")] = {
                    "path": current_visit(call.state).get("path", ""),
                    "merchant": call.state["probe"].get("current_merchant", ""),
                }
                update.setdefault("probe", {})["unfinished_merchants"] = unfinished
            return with_warning(
                with_state_update(result, update),
                call,
                "page_arrival_unconfirmed",
                f"Candidate {candidate['candidate_id']} targets {candidate['path']}. Read the page or screenshot, then call confirm_page with its observation reference.",
            )
        pending: PendingVisit | None = call.state["general"].get("pending")
        if pending is not None and call.name in OBSERVATION_TOOLS:
            observed: PendingVisit = {**pending, "observation_id": call.call_id}
            return with_warning(
                with_state_update(result, {"general": {"pending": observed}}),
                call,
                "page_observation",
                f"candidate_id={pending['candidate_id']}; observation_id={call.call_id}. Confirm only if this fresh evidence matches the candidate manual.",
            )
        if pending is not None and call.name == "tap_in_page":
            return with_state_update(result, {"general": {"pending": {**pending, "observation_id": ""}}})
        if call.name == "close_package":
            return with_state_update(
                result,
                combine_updates(
                    initial_page_context_state(),
                    {
                        "general": {"active": False},
                        "merchant": self._end_merchant(),
                        "probe": {"current_merchant": ""},
                    },
                ),
            )
        return result

    def confirm_page(
        self,
        state: ConductorState,
        candidate_id: str,
        observation_id: str,
        matches: bool,
        evidence: str,
        call_id: str,
    ) -> Command[str]:
        pending: PendingVisit | None = state["general"].get("pending")
        if pending is not None and pending["rejected"]:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content="Page was rejected. Exit/retry navigation to create a new candidate, then observe and confirm it. Training remains blocked.",
                            status="error",
                            name="confirm_page",
                            tool_call_id=call_id,
                        )
                    ]
                }
            )
        valid: bool = (
            pending is not None
            and pending["candidate_id"] == candidate_id
            and bool(observation_id)
            and pending["observation_id"] == observation_id
            and bool(evidence.strip())
        )
        if not valid or pending is None:
            return Command(
                update={
                    "messages": [
                        ToolMessage(
                            content="Invalid or stale page observation reference.",
                            status="error",
                            name="confirm_page",
                            tool_call_id=call_id,
                        )
                    ]
                }
            )
        if not matches:
            return Command(
                update=combine_updates(
                    complete_update(
                        state, {"general": {"pending": {**pending, "rejected": True, "observation_id": ""}}}
                    ),
                    {
                        "messages": [
                            ToolMessage(
                                content="Page mismatch recorded. Exit/retry navigation before confirming a new candidate; training remains blocked.",
                                name="confirm_page",
                                tool_call_id=call_id,
                            )
                        ]
                    },
                )
            )
        visit: PageVisit = {
            "visit_id": pending["candidate_id"],
            "path": pending["path"],
            "strategy": pending["strategy"],
        }
        update: ConductorUpdate = {
            "general": {"active": True, "visit": visit, "app_root": pending["app_root"], "pending": None},
            # The navigation label is a hint, not an extracted merchant identity.
            "probe": {"current_merchant": ""},
        }
        update["merchant"] = self._begin_merchant(visit) if is_merchant_visit(visit) else self._end_merchant()
        return Command(
            update={
                **complete_update(state, update),
                "messages": [
                    ToolMessage(content="Page arrival confirmed.", name="confirm_page", tool_call_id=call_id)
                ],
            }
        )
