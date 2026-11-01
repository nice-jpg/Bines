from src.conductor.state import current_visit
from src.device.services.execution import DeviceOperationExecutor
from src.strategy.merchant.visit_binding import activate_merchant_visit, deactivate_merchant_visit

"""Page strategy integration: declared selection, arrival, stages and evidence."""
import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command
from page_strategy_helpers import (
    apply,
    mechanism,
    message,
    page_controller,
    request,
    state,
    strategies,
)
from test_collection_persistence_middleware import _mark_trained, _middleware, _tool_message

from src.agent_runtime.strategy.base import Strategy
from src.agent_runtime.strategy.middleware import StrategyDispatchMiddleware
from src.conductor.state import ConductorState, merge_fields, read_conductor_state
from src.strategy.general.declarations import read_page_strategy, read_strategy_declaration


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
    with pytest.raises(ValueError):
        controller.wrap_tool_call(
            request("run_package", {"messages": []}, package_name="com.sankuai.meituan"),
            lambda r: calls.append(r) or message("run_package"),
        )
    assert not calls


def test_declaration_is_explicit_not_path_inference(tmp_path):
    page = mechanism(tmp_path)
    merchant = tmp_path / "meituan/外卖/美食/商家/PAGE.md"
    merchant.write_text(merchant.read_text().replace("merchant", "general"))
    assert read_page_strategy(page, "meituan/外卖/美食/商家") == "general"
    assert read_strategy_declaration(merchant) == "general"


@pytest.mark.parametrize("asynchronous", [False, True])
def test_arrival_accepts_conclusion_without_observation(tmp_path, asynchronous):
    controller = page_controller(mechanism(tmp_path))
    current = state()

    async def ah(r):
        return message(r.tool_call["name"])

    call = request("navigate_to", current, next_path="meituan/外卖/美食/商家", merchant_name="Shop")
    result = (
        asyncio.run(controller.awrap_tool_call(call, ah))
        if asynchronous
        else controller.wrap_tool_call(call, lambda r: message("navigate_to"))
    )
    apply(current, result)
    assert current["general"]["visit"]["path"] == "meituan/外卖/美食"
    assert current["general"]["visit"]["strategy"] == "general"
    candidate = current["general"]["arrival"]["candidate_id"]
    with pytest.raises(ValueError, match="stale"):
        controller.confirm_page(read_conductor_state(current), "stale-candidate", True, "confirm")
    assert (
        controller.wrap_tool_call(
            request("prepare_collector", current), lambda r: message("prepare_collector")
        ).status
        == "error"
    )
    result = controller.confirm_page(
        read_conductor_state(current),
        candidate,
        True,
        "confirm",
    )
    apply(current, result)
    assert current["general"]["arrival"]["status"] == "confirmed"
    current.update(activate_merchant_visit(current))
    assert current["general"]["visit"]["strategy"] == "merchant"
    assert current["merchant"]["phase"] == "awaiting_training"
    assert current["merchant"]["evidence"]["initialized"] is False
    assert "merchant_collection_done" not in result.update


def test_mismatch_clears_candidate_and_rejects_repeat_confirmation(tmp_path):
    controller = page_controller(mechanism(tmp_path))
    current = state()
    apply(
        current,
        controller.wrap_tool_call(
            request("navigate_back", current, next_path="meituan"), lambda r: message("navigate_back")
        ),
    )
    candidate = current["general"]["arrival"]["candidate_id"]
    apply(
        current,
        controller.confirm_page(
            read_conductor_state(current),
            candidate,
            False,
            "confirm",
        ),
    )
    assert current["general"]["arrival"] is None
    assert current["general"]["visit"] is None
    with pytest.raises(ValueError, match="stale"):
        controller.confirm_page(read_conductor_state(current), candidate, True, "confirm2")


def test_confirmation_schema_only_requires_candidate_and_conclusion(tmp_path):
    from src.strategy.general.tools import create_confirm_page_tool

    tool = create_confirm_page_tool(page_controller(mechanism(tmp_path)))
    schema = tool.tool_call_schema.model_json_schema()
    assert set(schema["properties"]) == {"candidate_id", "matches"}
    assert set(schema["required"]) == {"candidate_id", "matches"}


@pytest.mark.parametrize("name", ["page_view", "screenshot"])
def test_pending_queries_do_not_write_confirmation_state(tmp_path, name):
    controller = page_controller(mechanism(tmp_path))
    current = state()
    apply(
        current,
        controller.wrap_tool_call(
            request("navigate_to", current, next_path="meituan"), lambda _: message("navigate_to")
        ),
    )
    expected = message(name)
    arrival = dict(current["general"]["arrival"])
    assert controller.wrap_tool_call(request(name, current), lambda _: expected) is expected
    assert current["general"]["arrival"] == arrival


def test_confirm_without_pending_is_invalid(tmp_path):
    controller = page_controller(mechanism(tmp_path))
    with pytest.raises(ValueError, match="candidate"):
        controller.confirm_page(state(), "missing", True, "confirm")


def test_pending_manual_does_not_claim_confirmed_destination(tmp_path):
    persistence, _, _ = _middleware(tmp_path)
    general, _ = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current.setdefault("general", {})["arrival"] = {
        "candidate_id": "new",
        "path": "meituan/美食/商家",
        "strategy": "merchant",
        "app_root": "meituan",
        "package_name": "com.app",
        "merchant_name": "Shop",
        "status": "awaiting_confirmation",
    }
    content = general.before_model(read_conductor_state(current), None)["messages"][0].content
    assert "<candidate_page_manual>" in content and "<current_page_manual" not in content
    assert "confirm_page" not in content


def test_general_does_not_preempt_collection_resource_validation(tmp_path):
    persistence, _, _ = _middleware(tmp_path)
    general, _ = strategies(persistence.service)
    current = state("meituan")
    called = []
    for name in ("prepare_collector", "is_done", "collect_continuously"):
        assert general.wrap_tool_call(request(name, current), lambda r: message(name)).status == "success"
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
    assert result.status == "success"


def test_retraining_clears_current_evidence_but_retains_historical_completion(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    general, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current.update(
        merge_fields(
            current,
            {
                "merchant": {
                    "evidence": {"initialized": True, "consecutive_empty_swipes": 3},
                    "visit_id": current["general"]["visit"]["visit_id"],
                }
            },
        )
    )
    current["merchant"].update(phase="completed")
    current.setdefault("probe", {})["current_merchant"] = "商家A"
    current.setdefault("probe", {})["completed_merchants"] = {current["general"]["visit"]["path"]: ["商家A"]}

    def train(r):
        _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
        return message("prepare_collector")

    result = office.wrap_tool_call(request("prepare_collector", current), train)
    assert result.update["merchant"]["phase"] == "ready"
    assert result.update["merchant"]["evidence"]["initialized"] is False
    apply(current, result)
    apply(current, Command(update=office.before_model(current, None)))
    assert "visit-current" in current["probe"]["visit_summaries"]
    assert current["probe"]["completed_merchants"][current["general"]["visit"]["path"]] == ["商家A"]
    assert not persistence.service.completion(current).done


def test_script_change_invalidates_office_and_manual_completion(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current.update(
        merge_fields(
            current,
            {
                "merchant": {
                    "evidence": {"initialized": True, "consecutive_empty_swipes": 3},
                    "visit_id": current["general"]["visit"]["visit_id"],
                }
            },
        )
    )
    current["merchant"].update(phase="completed")
    script = persistence.service.mechanism.root / current["general"]["visit"]["path"] / "run.agent.py"
    script.write_text(script.read_text() + "\n# changed\n")
    result = office.wrap_tool_call(request("is_done", current), lambda r: pytest.fail("stale version"))
    assert result.update["merchant"]["phase"] == "awaiting_training"
    assert result.update["merchant"]["evidence"]["initialized"] is False
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
    current.update(
        merge_fields(
            current,
            {
                "merchant": {
                    "evidence": {"initialized": True, "consecutive_empty_swipes": 2},
                    "visit_id": current["general"]["visit"]["visit_id"],
                }
            },
        )
    )
    failed = message("swipe_up")
    failed.status = "error"
    result = office.wrap_tool_call(request("swipe_up", current), lambda r: failed)
    assert result.update["merchant"]["evidence"]["consecutive_empty_swipes"] == 0
    assert result.update["merchant"]["phase"] == "traversing"


def test_dispatcher_keeps_same_strategy_until_tool_returns():
    events = []

    class Recording(Strategy[ConductorState]):
        def wrap_tool_call(self, r, h):
            events.append("source-before")
            result = h(r)
            events.append("source-after")
            return result

    selected = Recording(constraints=[], features=[])

    from src.agent_runtime.strategy.registration import StrategyRegistration
    from src.agent_runtime.strategy.registry import StrategyRegistry
    from src.strategy.general.state import GeneralState, initial_general_state

    registry = StrategyRegistry()
    registry.register(StrategyRegistration("general", selected, GeneralState, initial_general_state))
    registry.validate_schema(ConductorState)
    dispatcher = StrategyDispatchMiddleware(
        state_schema=ConductorState, registry=registry
    )
    dispatcher.wrap_tool_call(request("talk", state()), lambda r: message("talk"))
    assert events == ["source-before", "source-after"]


def test_bound_training_version_cannot_be_replaced_silently(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current["merchant"].update(phase="ready", training_version="previous-version")
    result = office.wrap_tool_call(request("swipe_up", current), lambda r: pytest.fail("stale binding"))
    assert result.update["merchant"]["phase"] == "awaiting_training"
    assert "training_version_changed" in result.update["messages"][0].content


def test_recovery_does_not_create_global_obligation(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current.setdefault("probe", {})["current_merchant"] = "商家A"
    current["merchant"].update(phase="completed", training_version="previous-version")
    result = office.wrap_tool_call(
        request("navigate_back", current, next_path="meituan/美食", recovery_reason="repair"),
        lambda r: message("navigate_back"),
    )
    assert "unfinished_merchants" not in result.update.get("probe", {})
    apply(current, result)
    assert current_visit(current)["visit_id"] == ""


def test_entry_tap_is_allowed_once_before_traversal(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, office = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current["merchant"].update(phase="ready", training_version=persistence.service.training_version(current))
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

    hitl = CaptchaHumanInTheLoopMiddleware(
        authentication_tool=create_tool(authenticate_captcha),
        executor=DeviceOperationExecutor(),
    )
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



def test_inner_completion_revocation_is_not_overwritten_by_merchant_strategy(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    _, merchant = strategies(persistence.service)
    current = state("meituan/美食/商家")
    current["merchant"].update(
        phase="completed", training_version=persistence.service.training_version(current)
    )
    current["merchant"]["evidence"].update(initialized=True, consecutive_empty_swipes=0)
    result = merchant.wrap_tool_call(
        request("is_done", current),
        lambda call: persistence.is_done(SimpleNamespace(state=current, tool_call_id=call.tool_call["id"])),
    )
    assert result.update["merchant"]["phase"] == "traversing"
    assert result.update["merchant"]["visit_id"] == "visit-current"


def test_close_clears_visit_and_merchant_without_erasing_task_history(tmp_path):
    controller = page_controller(mechanism(tmp_path))
    current = state("meituan/美食/商家", probe={"completed_merchants": {"meituan/美食/商家": ["Shop"]}})
    current["probe"]["current_merchant"] = "Shop"
    result = controller.wrap_tool_call(
        request("close_package", current), lambda call: message("close_package")
    )
    apply(current, result)
    assert current["general"] == {"active": False, "app_root": "", "visit": None, "arrival": None}
    current.update(deactivate_merchant_visit(current))
    assert current["merchant"]["phase"] == "inactive"
    assert current["probe"]["current_merchant"] == ""
    assert current["probe"]["completed_merchants"] == {"meituan/美食/商家": ["Shop"]}


def test_navigation_name_is_not_a_collection_identity_constraint(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    current = state("meituan/美食/商家")
    current["probe"]["current_merchant"] = "商加A"  # Navigation typo, not extracted data.
    _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id="visit-current")
    assert persistence.service.training_error(current) == ""
    apply(current, Command(update=persistence.service.initialize(current).update))
    assert current["probe"]["current_merchant"] == "商家A"


def test_typo_confirmation_training_and_collection_use_observed_profile(tmp_path):
    persistence, registry, _ = _middleware(tmp_path)
    mechanism(persistence.service.mechanism.root)
    general, merchant = strategies(persistence.service)
    current = state("meituan/美食")
    apply(
        current,
        general.wrap_tool_call(
            request("navigate_to", current, next_path="meituan/美食/商家", merchant_name="商加A"),
            lambda r: message("navigate_to"),
        ),
    )
    assert persistence.service.training_error(current) == "page_confirmation_required"
    assert persistence.service.initialize(current).error == "page_confirmation_required"
    observe = request("page_view", current)
    apply(current, general.wrap_tool_call(observe, lambda r: message("page_view")))
    pending = current["general"]["arrival"]
    apply(
        current,
        page_controller(persistence.service.mechanism).confirm_page(
            current,
            pending["candidate_id"],
            True,
            "confirm",
        ),
    )
    current.update(activate_merchant_visit(current))
    visit_id = current["general"]["visit"]["visit_id"]

    def train(r):
        _mark_trained(registry, mechanism=persistence.service.mechanism, page_visit_id=visit_id)
        return message("prepare_collector")

    apply(current, merchant.wrap_tool_call(request("prepare_collector", current), train))
    apply(current, Command(update=merchant.before_model(current, None)))
    assert current["merchant"]["phase"] == "ready"
    assert current["probe"]["current_merchant"] == "商家A"
    assert persistence.service.training_error(current) == ""
    result = merchant.wrap_tool_call(request("swipe_up", current), lambda r: _tool_message("swipe_up"))
    apply(current, result)
    assert current["merchant"]["phase"] == "traversing"
    assert current["merchant"]["evidence"]["initialized"]
    assert current["probe"]["visit_summaries"][visit_id]["collected_items"] == 1
