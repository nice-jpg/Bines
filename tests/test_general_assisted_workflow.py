"""General strategy assists decisions without imposing a business lifecycle."""

import asyncio

import pytest
from langchain_core.messages import AIMessage
from page_strategy_helpers import apply, mechanism, message, page_controller, request, state

from src.agent_runtime.middleware.tool_error import ToolErrorMiddleware
from src.conductor.progress import summarize_probe_progress
from src.conductor.state import current_visit
from src.device.services.execution import DeviceOperationExecutor
from src.strategy.general.visit_history import VisitHistoryMiddleware
from src.strategy.merchant.visit_binding import activate_merchant_visit, deactivate_merchant_visit


def confirm_candidate(page, current, matches=True):
    arrival = current["general"]["arrival"]
    result = page.confirm_page(
        current,
        arrival["candidate_id"],
        matches,
        "confirm",
    )
    apply(current, result)
    return result


@pytest.mark.parametrize("asynchronous", [False, True])
def test_pending_blocks_actions_until_confirmation_then_mismatch_allows_navigation(tmp_path, asynchronous):
    page = page_controller(mechanism(tmp_path))
    current = state()
    apply(
        current,
        page.wrap_tool_call(
            request("navigate_to", current, next_path="meituan/美食/商家"), lambda _: message("navigate_to")
        ),
    )
    candidate = current["general"]["arrival"]["candidate_id"]
    assert current_visit(current)["visit_id"] == ""
    assert current["active_strategy"] == "general"
    for name in ("swipe_up", "tap_in_page", "close_package", "is_done", "navigate_back"):
        called = []

        async def handler(call):
            called.append(name)
            return message(name)

        result = (
            asyncio.run(page.awrap_tool_call(request(name, current), handler))
            if asynchronous
            else page.wrap_tool_call(request(name, current), lambda _: called.append(name) or message(name))
        )
        assert called == []
        assert result.status == "error"
    confirm_candidate(page, current, False)
    assert current["general"]["arrival"] is None
    assert not current_visit(current)["visit_id"]
    apply(
        current,
        page.wrap_tool_call(
            request("navigate_to", current, next_path="meituan/美食/商家"), lambda _: message("navigate_to")
        ),
    )
    result = confirm_candidate(page, current)
    assert result.update["messages"][0].status == "success"
    assert current_visit(current)["visit_id"] != candidate
    assert current["active_strategy"] == "merchant"


def test_navigation_error_and_invalid_target_do_not_commit_pending(tmp_path):
    page = page_controller(mechanism(tmp_path))
    current = state()
    denied = ToolErrorMiddleware().wrap_tool_call(
        request("navigate_to", current, next_path="../escape"),
        lambda call: page.wrap_tool_call(call, lambda _: pytest.fail("invalid path dispatched")),
    )
    assert denied.status == "error"
    assert current_visit(current)["visit_id"]
    failed = message("navigate_to")
    failed.status = "error"
    apply(
        current, page.wrap_tool_call(request("navigate_to", current, next_path="meituan"), lambda _: failed)
    )
    assert current_visit(current)["visit_id"]
    assert current["general"].get("arrival") is None


def test_invalid_navigation_coordinates_leave_page_unchanged(tmp_path):
    from src.tools.shared.factory import create_tool

    def navigate_to(x: int, y: int, next_path: str) -> str:
        """Navigate with coordinates."""
        pytest.fail("bad coordinates dispatched")

    page = page_controller(mechanism(tmp_path))
    current = state()
    call = request("navigate_to", current, x="invalid", y=1, next_path="meituan")
    call.tool = create_tool(navigate_to)
    result = ToolErrorMiddleware().wrap_tool_call(
        call,
        lambda call: page.wrap_tool_call(call, lambda _: pytest.fail("bad coordinates entered features")),
    )
    assert result.status == "error"
    assert current_visit(current)["visit_id"] == "visit-current"


def test_binding_and_history_do_not_change_during_navigation(tmp_path):
    page = page_controller(mechanism(tmp_path))
    current = state("meituan/美食/商家")
    history = VisitHistoryMiddleware()
    update = history.before_model(current, None)
    current.update(update)
    apply(
        current,
        page.wrap_tool_call(
            request("navigate_back", current, next_path="meituan"), lambda _: message("navigate_back")
        ),
    )
    assert current["merchant"]["visit_id"] == "visit-current"  # Not owned by PageVisit.
    current.update(deactivate_merchant_visit(current))
    current.update(history.before_model(current, None))
    assert current["merchant"]["phase"] == "inactive"
    assert history.before_model(current, None) is None
    confirm_candidate(page, current)
    current.update(history.before_model(current, None))
    assert [fact["event"] for fact in current["probe"]["visit_history"]] == ["entered", "exited", "entered"]
    current["probe"]["target_leaf_paths"] = ["meituan", "meituan/外卖"]
    progress = summarize_probe_progress(current)
    assert progress["not_yet_visited"] == ["meituan/外卖"]
    assert "authorized" not in progress
    apply(current, page.wrap_tool_call(request("close_package", current), lambda _: message("close_package")))
    current.update(history.before_model(current, None))
    assert current["probe"]["visit_history"][-1]["event"] == "closed"


def test_new_merchant_binding_does_not_inherit_old_evidence():
    current = state("meituan/美食/商家")
    current["merchant"]["evidence"]["initialized"] = True
    current["general"]["visit"]["visit_id"] = "next"
    update = activate_merchant_visit(current)
    assert update["merchant"]["visit_id"] == "next"
    assert update["merchant"]["evidence"]["initialized"] is False



def test_cancelled_async_caller_does_not_release_running_device():
    from src.device.services.execution import DeviceOperationBusy

    async def scenario():
        executor = DeviceOperationExecutor()
        started, finish = asyncio.Event(), asyncio.Event()

        async def work():
            started.set()
            await finish.wait()
            return "done"

        caller = asyncio.create_task(executor.aexecute(work))
        await started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        with pytest.raises(DeviceOperationBusy):
            executor.execute(lambda: None)
        finish.set()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert executor.execute(lambda: "next") == "next"

    asyncio.run(scenario())


def test_progress_and_manual_preserve_prior_message_prefix(tmp_path):
    from langgraph.graph.message import add_messages

    from src.strategy.general.context.middleware import PageInputMiddleware

    context = PageInputMiddleware(mechanism=mechanism(tmp_path))
    current = state()
    current["probe"].update(target_leaf_paths=["meituan"], run_id="task")
    first = context.before_model(current, None)
    current["messages"] = add_messages(current["messages"], first["messages"])
    saved = list(current["messages"])
    assert context.before_model(current, None) is None
    current["probe"]["visit_history"] = [{"visit_id": "home", "path": "meituan", "event": "entered"}]
    second = context.before_model(current, None)
    projected = add_messages(current["messages"], second["messages"])
    assert projected[: len(saved)] == saved
    assert len(projected) == len(saved) + 1


def test_page_manual_read_failure_does_not_block_general(tmp_path):
    from src.strategy.general.context.middleware import PageInputMiddleware

    page = mechanism(tmp_path)
    (tmp_path / "meituan/外卖/美食/PAGE.md").unlink()
    result = PageInputMiddleware(mechanism=page).before_model(state(), None)
    assert "unavailable" in result["messages"][0].content


def test_context_delivers_manual_without_repeating_arrival_feedback(tmp_path):
    from langgraph.graph.message import add_messages

    from src.strategy.general.context.middleware import PageInputMiddleware

    page = mechanism(tmp_path)
    controller = page_controller(page)
    context = PageInputMiddleware(mechanism=page)
    current = state()
    navigation = controller.wrap_tool_call(
        request("navigate_to", current, next_path="meituan/美食"),
        lambda _: message("navigate_to"),
    )
    current.update(navigation.update)
    pending = context.before_model(current, None)
    assert len(pending["messages"]) == 1
    assert "<candidate_page_manual>" in pending["messages"][0].content
    assert "confirm_page" not in pending["messages"][0].content
    current["messages"] = add_messages(current["messages"], pending["messages"])
    assert context.before_model(current, None) is None

    confirmation = controller.confirm_page(
        current, current["general"]["arrival"]["candidate_id"], True, "confirm"
    )
    current.update({key: value for key, value in confirmation.update.items() if key != "messages"})
    current["messages"] = add_messages(current["messages"], confirmation.update["messages"])
    confirmed = context.before_model(current, None)
    assert len(confirmed["messages"]) == 1
    assert "<current_page_manual " in confirmed["messages"][0].content
    assert not any(message.id and message.id.startswith("page_identity:") for message in confirmed["messages"])


def test_merchant_model_hook_only_invalidates_state(tmp_path):
    from src.strategy.merchant.workflow import MerchantWorkflowMiddleware
    from test_collection_persistence_middleware import _middleware

    persistence, _, _ = _middleware(tmp_path)
    workflow = MerchantWorkflowMiddleware(persistence.service)
    current = state("meituan/美食/商家")
    assert workflow.before_model(current, None) is None
    current["merchant"].update(phase="ready", training_version="obsolete")
    update = workflow.before_model(current, None)
    assert update["merchant"]["phase"] == "awaiting_training"
    assert "messages" not in update
    assert asyncio.run(workflow.abefore_model(current, None)) == update


def test_collection_contract_is_not_navigation_requirement(tmp_path):
    page = mechanism(tmp_path)
    path = tmp_path / "meituan/美食/商家/PAGE.md"
    path.write_text("---\npage_strategy: merchant\n---\nMerchant layout")
    result = page_controller(page).wrap_tool_call(
        request("navigate_to", state(), next_path="meituan/美食/商家"), lambda _: message("navigate_to")
    )
    assert result.update["general"]["arrival"]["strategy"] == "merchant"


def test_merchant_talk_does_not_emit_redundant_state_update(tmp_path):
    from page_strategy_helpers import strategies
    from test_collection_persistence_middleware import _middleware

    persistence, _, _ = _middleware(tmp_path)
    _, merchant = strategies(persistence.service)
    current = state("meituan/美食/商家")
    for name in ("talk", "get_probe_progress"):
        result = merchant.wrap_tool_call(request(name, current), lambda _: message(name))
        assert result.status == "success"


def test_captcha_is_not_suppressed_by_other_tool_calls():
    from src.strategy.general.captcha import CaptchaHumanInTheLoopMiddleware

    hitl = CaptchaHumanInTheLoopMiddleware(
        authentication_tool=None,
        executor=DeviceOperationExecutor(),
    )
    calls = [
        {"name": "swipe_up", "args": {}, "id": "first"},
        {"name": "authenticate_captcha", "args": {}, "id": "second"},
    ]
    current = state()
    current["messages"] = [AIMessage(content="", tool_calls=calls)]
    assert hitl._should_interrupt(calls[1], hitl.interrupt_on["authenticate_captcha"], current, None)
