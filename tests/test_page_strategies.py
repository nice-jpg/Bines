"""Page strategy integration: declared selection, arrival, stages and evidence."""
from page_strategy_helpers import page_controller

from src.conductor.state import read_conductor_state, merge_fields


import asyncio
import hashlib
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.types import Command
from page_strategy_helpers import mechanism, state, request, message, apply, strategies
from test_collection_persistence_middleware import _middleware, _mark_trained, _tool_message
from src.strategy.general.visits import PageVisitController, initial_page_context_state
from page_strategy_helpers import new_visit
from src.conductor.state import ConductorState, read_conductor_state
from src.agent_runtime.strategy.base import Strategy
from src.agent_runtime.strategy.middleware import StrategyDispatchMiddleware
from src.strategy.general.declarations import read_strategy_declaration, read_page_strategy


@pytest.mark.parametrize(
    "declaration",
    [
        "",
        "---\npage_strategy: other\n---\n",
        "---\npage_strategy: general\npage_strategy: merchant\n---\n",
        "---\npage_strategy: [general]\n---\n",
    ],
)
def test_invalid_declaration_prevents_navigation(tmp_path, declaration):
    page = mechanism(tmp_path)
    (tmp_path / "meituan/PAGE.md").write_text(declaration)
    controller = page_controller(page)
    calls = []
    result = controller.execute_operation(
        request("run_package", {"messages": []}, package_name="com.sankuai.meituan"),
        lambda r: calls.append(r) or message("run_package"),
    )
    assert not calls and result.status == "error"


def test_declaration_is_explicit_not_path_inference(tmp_path):
    page = mechanism(tmp_path)
    merchant = tmp_path / "meituan/外卖/美食/商家/PAGE.md"
    merchant.write_text(merchant.read_text().replace("merchant", "general"))
    assert read_page_strategy(page, "meituan/外卖/美食/商家") == "general"
    assert read_strategy_declaration(merchant) == "general"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_arrival_requires_fresh_evidence_and_switches_only_on_confirmation(tmp_path, asynchronous):
    controller = page_controller(mechanism(tmp_path))
    current = state()

    async def ah(r):
        return message(r.tool_call["name"])

    call = request("navigate_to", current, next_path="meituan/外卖/美食/商家", merchant_name="Shop")
    result = (
        asyncio.run(controller.aexecute_operation(call, ah))
        if asynchronous
        else controller.execute_operation(call, lambda r: message("navigate_to"))
    )
    apply(current, result)
    assert current['general']['visit']['path'] == "meituan/外卖/美食"
    assert current["general"]["visit"]["strategy"] == "general"
    candidate = current['general']['pending']["candidate_id"]
    result = controller.confirm_page(
        read_conductor_state(current), candidate, "old", True, "merchant card", "confirm"
    )
    assert result.update["messages"][0].status == "error"
    assert (
        controller.execute_operation(
            request("prepare_collector", current), lambda r: pytest.fail("must block")
        ).status
        == "error"
    )
    observe = request("page_view", current)
    apply(current, controller.execute_operation(observe, lambda r: message("page_view")))
    result = controller.confirm_page(
        read_conductor_state(current),
        candidate,
        observe.tool_call["id"],
        True,
        "Visible merchant name and menu match",
        "confirm",
    )
    apply(current, result)
    assert current['general']['pending'] is None
    assert current["general"]["visit"]["strategy"] == "merchant"
    assert current["merchant"]["phase"] == "awaiting_training"
    assert current['merchant']['evidence']['initialized'] is False
    assert "merchant_collection_done" not in result.update


def test_mismatch_and_dismissal_invalidate_observation(tmp_path):
    controller = page_controller(mechanism(tmp_path))
    current = state()
    apply(
        current,
        controller.execute_operation(
            request("navigate_back", current, next_path="meituan"), lambda r: message("navigate_back")
        ),
    )
    obs = request("screenshot", current)
    apply(current, controller.execute_operation(obs, lambda r: message("screenshot")))
    candidate = current['general']['pending']["candidate_id"]
    apply(
        current,
        controller.confirm_page(
            read_conductor_state(current),
            candidate,
            obs.tool_call["id"],
            False,
            "still at prior page",
            "confirm",
        ),
    )
    assert current['general']['pending']["rejected"]
    assert not current['general']['pending']["observation_id"]
    assert current['general']['visit']['path'] == "meituan/外卖/美食"
    assert (
        controller.confirm_page(
            read_conductor_state(current), candidate, obs.tool_call["id"], True, "retry", "confirm2"
        )
        .update["messages"][0]
        .status
        == "error"
    )


def test_pending_manual_does_not_claim_confirmed_destination(tmp_path):
    persistence, _, _ = _middleware(tmp_path)
    general, _ = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current.setdefault('general', {})['pending'] = {
        "candidate_id": "new",
        "path": "meituan/美食/商家",
        "strategy": "merchant",
        "app_root": "meituan",
        "package_name": "com.app",
        "merchant_name": "Shop",
        "observation_id": "",
        "rejected": False,
    }
    content = general.before_model(read_conductor_state(current), None)["messages"][0].content
    assert "UNCONFIRMED" in content and "<current_page_manual" not in content


def test_general_page_never_trains_or_collects(tmp_path):
    persistence, _, _ = _middleware(tmp_path)
    general, _ = strategies(persistence.service)
    current = state("meituan")
    called = []
    for name in ("prepare_collector", "is_done", "collect_continuously"):
        assert (
            general.wrap_tool_call(request(name, current), lambda r: pytest.fail("office tool")).status
            == "error"
        )
    result = general.wrap_tool_call(
        request("swipe_up", current), lambda r: called.append(r) or message("swipe_up")
    )
    assert called and not isinstance(result, Command)


def test_untrained_office_blocks_product_actions_but_allows_observation(tmp_path):
    persistence, _, _ = _middleware(tmp_path)
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    for name in ("swipe_up", "collect_continuously", "tap_in_page"):
        result = office.wrap_tool_call(request(name, current), lambda r: pytest.fail("must not execute"))
        assert result.update["messages"][0].status == "error"
    result = office.wrap_tool_call(request("screenshot", current), lambda r: message("screenshot"))
    assert result.update["merchant"]["phase"] == "awaiting_training"


def test_retraining_clears_old_completion_and_duplicate_identity(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    general, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current.update(merge_fields(current, {'merchant': {'evidence': {'initialized': True, 'consecutive_empty_swipes': 3}, 'visit_id': current["general"]["visit"]["visit_id"]}}))
    current["merchant"].update(phase="completed")
    current.setdefault('probe', {})['current_merchant'] = "商家A"
    current.setdefault('probe', {})['completed_merchants'] = {current['general']['visit']['path']: ("商家A",)}

    def train(r):
        _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
        return message("prepare_collector")

    result = office.wrap_tool_call(request("prepare_collector", current), train)
    assert result.update["merchant"]["phase"] == "ready"
    assert "visit-current" in result.update["probe"]["visit_summaries"]
    assert result.update['merchant']['evidence']['initialized'] is False
    assert result.update['probe']['completed_merchants'][current['general']['visit']['path']] == ()
    apply(current, result)
    assert not persistence.service.completion(current).done


def test_script_change_invalidates_office_and_manual_completion(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current.update(merge_fields(current, {'merchant': {'evidence': {'initialized': True, 'consecutive_empty_swipes': 3}, 'visit_id': current["general"]["visit"]["visit_id"]}}))
    current["merchant"].update(phase="completed")
    script = persistence.service.mechanism.root / current['general']['visit']['path'] / "run.agent.py"
    script.write_text(script.read_text() + "\n# changed\n")
    result = office.wrap_tool_call(request("is_done", current), lambda r: pytest.fail("stale version"))
    assert result.update["merchant"]["phase"] == "awaiting_training"
    assert result.update['merchant']['evidence']['initialized'] is False
    assert not persistence.service.completion(current).done


def test_device_failure_breaks_completion_evidence(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current["merchant"].update(
        training_version=persistence.service.training_version(current),
        phase="traversing",
    )
    current.update(merge_fields(current, {'merchant': {'evidence': {'initialized': True, 'consecutive_empty_swipes': 2}, 'visit_id': current["general"]["visit"]["visit_id"]}}))
    failed = message("swipe_up")
    failed.status = "error"
    result = office.wrap_tool_call(request("swipe_up", current), lambda r: failed)
    assert result.update['merchant']['evidence']['consecutive_empty_swipes'] == 0
    assert result.update["merchant"]["phase"] == "traversing"


def test_same_response_training_claim_prevents_navigation(tmp_path):
    controller = page_controller(mechanism(tmp_path))
    current = state()
    current["messages"] = [
        AIMessage(
            content="",
            id="step",
            tool_calls=[
                {"name": "prepare_collector", "id": "train", "args": {}},
                {"name": "navigate_back", "id": "nav", "args": {"next_path": "meituan"}},
            ],
        )
    ]
    call = request("navigate_back", current, next_path="meituan")
    call.tool_call["id"] = "nav"
    assert controller.execute_operation(call, lambda r: pytest.fail("must reject")).status == "error"


def test_whole_operation_mutex_rejects_concurrent_page_action(tmp_path):
    controller = page_controller(mechanism(tmp_path))
    entered, release = Event(), Event()

    def hold(r):
        entered.set()
        assert release.wait(5)
        return message("page_view")

    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(controller.execute_operation, request("page_view", state()), hold)
        assert entered.wait(5)
        try:
            result = controller.execute_operation(
                request("screenshot", state()), lambda r: pytest.fail("concurrent action")
            )
            assert result.status == "error"
        finally:
            release.set()
        future.result()


def test_dispatcher_keeps_same_strategy_until_tool_returns():
    events = []

    class Recording(Strategy[ConductorState]):
        def wrap_tool_call(self, r, h):
            events.append("source-before")
            result = h(r)
            events.append("source-after")
            return result

    selected = Recording()

    def resolve(s):
        events.append("resolve")
        return "general"

    from src.agent_runtime.strategy.registry import StrategyRegistry
    from src.agent_runtime.strategy.registration import StrategyRegistration
    from src.strategy.general.state import GeneralState, initial_general_state
    registry = StrategyRegistry()
    registry.register(StrategyRegistration("general", selected, GeneralState, initial_general_state))
    registry.validate_schema(ConductorState)
    dispatcher = StrategyDispatchMiddleware(
        state_schema=ConductorState, registry=registry, select_strategy=resolve
    )
    dispatcher.wrap_tool_call(request("talk", state()), lambda r: message("talk"))
    assert events == ["resolve", "source-before", "source-after"]


def test_wrong_package_rejected_before_close(tmp_path):
    controller = page_controller(mechanism(tmp_path))
    result = controller.execute_operation(
        request("close_package", state(probe_package_name="com.expected"), package_name="com.other"),
        lambda r: pytest.fail("wrong app"),
    )
    assert result.status == "error"


def test_bound_training_version_cannot_be_replaced_silently(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current["merchant"].update(phase="ready", training_version="previous-version")
    result = office.wrap_tool_call(request("swipe_up", current), lambda r: pytest.fail("stale binding"))
    assert result.update["merchant"]["phase"] == "awaiting_training"
    assert "training_version_changed" in result.update["messages"][0].content


def test_recovery_from_stale_completed_visit_retains_obligation(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current.setdefault('probe', {})['current_merchant'] = "商家A"
    current["merchant"].update(phase="completed", training_version="previous-version")
    result = office.wrap_tool_call(
        request("navigate_back", current, next_path="meituan/美食", recovery_reason="repair"),
        lambda r: message("navigate_back"),
    )
    assert result.update['probe']['unfinished_merchants']["visit-current"]["merchant"] == "商家A"


def test_entry_tap_is_allowed_once_before_traversal(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current["merchant"].update(
        phase="ready", training_version=persistence.service.training_version(current)
    )
    result = office.wrap_tool_call(request("tap_in_page", current), lambda r: message("tap_in_page"))
    apply(current, result)
    assert current["merchant"]["entry_taps"] == 1
    result = office.wrap_tool_call(request("tap_in_page", current), lambda r: pytest.fail("second entry tap"))
    assert "merchant_entry_tap_limit" in result.content
    current["merchant"].update(phase="traversing", entry_taps=0)
    result = office.wrap_tool_call(request("tap_in_page", current), lambda r: pytest.fail("traversal tap"))
    assert "merchant_entry_tap_limit" in result.content


def test_captcha_start_failure_can_retry_same_call(monkeypatch):
    from src.strategy.general.captcha import CaptchaHumanInTheLoopMiddleware
    from src.tools.shared.factory import create_tool

    attempts = []

    def authenticate_captcha(reason: str) -> str:
        """Start human captcha access."""
        attempts.append(reason)
        if len(attempts) == 1:
            raise RuntimeError("temporarily unavailable")
        return "started"

    hitl = CaptchaHumanInTheLoopMiddleware(authentication_tool=create_tool(authenticate_captcha))
    monkeypatch.setattr(hitl, "_should_interrupt", lambda *args: True)
    current = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "authenticate_captcha",
                        "args": {"reason": "captcha"},
                        "id": "retry-call",
                    }
                ],
            )
        ]
    }
    with pytest.raises(RuntimeError, match="temporarily unavailable"):
        hitl._start_pending_authentication_sessions(current, object())
    hitl._start_pending_authentication_sessions(current, object())
    hitl._start_pending_authentication_sessions(current, object())
    assert attempts == ["captcha", "captcha"]


def test_old_checkpoint_progress_is_normalized_once():
    from src.conductor.state import read_conductor_state, collection_completed

    snapshot = read_conductor_state({
        "page_visit": {
            "kind": "merchant",
            "path": "meituan/美食/商家",
            "visit_id": "legacy",
            "phase": "completed",
            "training_version": "version",
            "entry_taps": 1,
            "progress": {"latest_new_count": 0, "empty_swipes": 3, "last_swipe": "swipe_up"},
        },
    })
    state = read_conductor_state(snapshot)
    assert state['general']['visit']['path'] == "meituan/美食/商家"
    assert state['general']['visit']['visit_id'] == state['merchant']['visit_id'] == "legacy"
    assert state['merchant']['evidence']['consecutive_empty_swipes'] == 3
    assert collection_completed(state)
    assert set(state["general"]["visit"]) == {"path", "visit_id", "strategy"}
    assert "page_visit" not in state
    assert read_conductor_state(state) == state


def test_inner_completion_revocation_is_not_overwritten_by_merchant_strategy(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, merchant = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current["merchant"].update(phase="completed", training_version=persistence.service.training_version(current))
    current["merchant"]["evidence"].update(initialized=True, consecutive_empty_swipes=0)
    result = merchant.wrap_tool_call(request("is_done", current), lambda call: persistence.is_done(SimpleNamespace(state=current, tool_call_id=call.tool_call["id"])))
    assert result.update["merchant"]["phase"] == "traversing"
    assert result.update["merchant"]["visit_id"] == "visit-current"


def test_close_clears_visit_and_merchant_without_erasing_task_history(tmp_path):
    controller = page_controller(mechanism(tmp_path))
    current = state("meituan/美食/商家", probe_completed_merchants={"meituan/美食/商家": ("Shop",)})
    current["probe"]["current_merchant"] = "Shop"
    result = controller.execute_operation(request("close_package", current), lambda call: message("close_package"))
    apply(current, result)
    assert current["general"] == {"active": False, "app_root": "", "visit": None, "pending": None}
    assert current["merchant"]["phase"] == "inactive"
    assert current["probe"]["current_merchant"] == ""
    assert current["probe"]["completed_merchants"] == {"meituan/美食/商家": ("Shop",)}


def test_navigation_name_is_not_a_collection_identity_constraint(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    current = state("meituan/美食/商家")
    current["probe"]["current_merchant"] = "商加A"  # Navigation typo, not extracted data.
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    assert persistence.service.training_error(current) == ""
    apply(current, Command(update=persistence.service.initialize(current).update))
    assert current["probe"]["current_merchant"] == "商家A"


def test_rejected_candidate_requires_new_navigation_even_after_fresh_observation(tmp_path):
    controller = page_controller(mechanism(tmp_path))
    current = state()
    apply(current, controller.execute_operation(
        request("navigate_back", current, next_path="meituan"), lambda r: message("navigate_back")))
    observe = request("page_view", current)
    apply(current, controller.execute_operation(observe, lambda r: message("page_view")))
    candidate = current["general"]["pending"]["candidate_id"]
    apply(current, controller.confirm_page(current, candidate, observe.tool_call["id"], False,
                                           "Wrong page", "reject"))
    observe = request("screenshot", current)
    apply(current, controller.execute_operation(observe, lambda r: message("screenshot")))
    result = controller.confirm_page(current, candidate, observe.tool_call["id"], True, "retry", "accept")
    assert result.update["messages"][0].status == "error"
    assert "navigation" in result.update["messages"][0].content
    assert controller.execute_operation(
        request("prepare_collector", current), lambda r: pytest.fail("rejected page")).status == "error"
    apply(current, controller.execute_operation(
        request("navigate_back", current, next_path="meituan"), lambda r: message("navigate_back")))
    observe = request("page_view", current)
    apply(current, controller.execute_operation(observe, lambda r: message("page_view")))
    pending = current["general"]["pending"]
    assert pending["candidate_id"] != candidate
    apply(current, controller.confirm_page(current, pending["candidate_id"], observe.tool_call["id"],
                                           True, "Home page visible", "confirm"))
    assert current["general"]["pending"] is None


@pytest.mark.parametrize("asynchronous", [False, True])
def test_navigation_clears_page_state_without_lifecycle_restoring_target_name(tmp_path, asynchronous):
    from src.middleware.probe.lifecycle import ProbeLifecycleMiddleware
    from src.strategy.merchant.state import initial_merchant_state

    page = mechanism(tmp_path)
    controller = page_controller(page)
    lifecycle = ProbeLifecycleMiddleware(mechanism=page)
    current = state("meituan/美食/商家")
    current["probe"].update(current_merchant="Previous", status="running",
                            completed_merchants={"old": ("Done",)})
    current["merchant"].update(phase="completed", training_version="old", entry_taps=1)
    current["merchant"]["evidence"].update(initialized=True, consecutive_empty_swipes=3)
    call = request("navigate_to", current, next_path="meituan/外卖/美食/商家", merchant_name="Typo")

    async def device(r):
        return message("navigate_to")

    async def navigate(r):
        return await controller.aexecute_operation(r, device)

    result = (
        asyncio.run(lifecycle.awrap_tool_call(call, navigate)) if asynchronous
        else lifecycle.wrap_tool_call(call, lambda r: controller.execute_operation(r, lambda r: message("navigate_to")))
    )
    apply(current, result)
    assert current["probe"]["current_merchant"] == ""
    assert current["merchant"] == initial_merchant_state()
    assert current["probe"]["completed_merchants"] == {"old": ("Done",)}
    assert not current["probe"].get("visited_leaf_paths")
    observe = request("page_view", current)
    apply(current, controller.execute_operation(observe, lambda r: message("page_view")))
    pending = current["general"]["pending"]
    apply(current, controller.confirm_page(current, pending["candidate_id"], observe.tool_call["id"],
                                           True, "Correct destination; name typo only", "confirm"))
    assert current["probe"]["current_merchant"] == ""
    assert current["merchant"]["phase"] == "awaiting_training"


def test_typo_confirmation_training_and_collection_use_observed_profile(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    mechanism(persistence.service.mechanism.root)
    general, merchant = strategies(persistence.service)
    current = state("meituan/美食")
    apply(current, general.wrap_tool_call(
        request("navigate_to", current, next_path="meituan/美食/商家", merchant_name="商加A"),
        lambda r: message("navigate_to")))
    assert persistence.service.training_error(current) == "page_confirmation_required"
    assert persistence.service.initialize(current).error == "page_confirmation_required"
    observe = request("page_view", current)
    apply(current, general.wrap_tool_call(observe, lambda r: message("page_view")))
    pending = current["general"]["pending"]
    apply(current, general.controller.confirm_page(
        current, pending["candidate_id"], observe.tool_call["id"], True,
        "商家A is the intended merchant; 商加A was a typo", "confirm"))
    visit_id = current["general"]["visit"]["visit_id"]

    def train(r):
        _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id=visit_id)
        return message("prepare_collector")

    apply(current, merchant.wrap_tool_call(request("prepare_collector", current), train))
    assert current["merchant"]["phase"] == "ready"
    assert current["probe"]["current_merchant"] == "商家A"
    assert persistence.service.training_error(current) == ""
    result = merchant.wrap_tool_call(request("swipe_up", current), lambda r: _tool_message("swipe_up"))
    apply(current, result)
    assert current["merchant"]["phase"] == "traversing"
    assert current["merchant"]["evidence"]["initialized"]
    assert current["probe"]["visit_summaries"][visit_id]["collected_items"] == 1
