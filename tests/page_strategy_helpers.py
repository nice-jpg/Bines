"""Explicit page fixtures and tool requests for the strategy workflow."""
from src.conductor.state import read_conductor_state, merge_fields


from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import ToolMessage

from src.strategy.general.visits import PageVisitController
from src.page.collection_contract import CollectionContract
from page_helpers import page_mechanism


def new_visit(kind):
    # Legacy checkpoint fixture; production readers translate it once.
    return {"kind": kind, **({"phase": "awaiting_training", "training_version": "", "entry_taps": 0} if kind == "merchant" else {})}


def page_controller(mechanism):
    from src.strategy.merchant.strategy import MerchantStrategy
    return PageVisitController(mechanism, strategy_names=frozenset({"general", "merchant"}), begin_merchant=MerchantStrategy.begin_visit, end_merchant=MerchantStrategy.end_visit)


def mechanism(root):
    for path in (
        "meituan",
        "meituan/外卖",
        "meituan/外卖/美食",
        "meituan/外卖/美食/商家",
        "meituan/美食",
        "meituan/美食/商家",
    ):
        directory = root / path
        directory.mkdir(parents=True, exist_ok=True)
        kind = (
            "merchant"
            if path in {"meituan/外卖/美食/商家", "meituan/美食/商家"}
            else "general"
        )
        (directory / "PAGE.md").write_text(f"---\npage_strategy: {kind}\n---\n\nPage {path}")
        (directory / "task.md").write_text("Page task")
    result = page_mechanism(root)
    for path in ("meituan/外卖/美食/商家", "meituan/美食/商家"):
        result.save_collection_contract(
            path, CollectionContract.from_dict({"primary_key": ["name"], "required_fields": ["name"]})
        )
    return result


def state(path="meituan/外卖/美食", **extra):
    kind = (
        "merchant"
        if path in {"meituan/外卖/美食/商家", "meituan/美食/商家"}
        else "general"
    )
    return read_conductor_state({
        "page_context_active": True,
        "page_context_path": path,
        "page_context_app_root": "meituan",
        "page_visit_id": "visit-current",
        "page_visit": new_visit(kind),
        "messages": [],
        **extra,
    })


def request(name, current, **args):
    return SimpleNamespace(
        tool_call={"name": name, "args": args, "id": uuid4().hex},
        state=read_conductor_state(current),
        tool=None,
        runtime=SimpleNamespace(config={}),
    )


def message(name, call_id="call"):
    return ToolMessage(content="ok", name=name, tool_call_id=call_id)


def apply(current, result):
    from langgraph.types import Command

    if isinstance(result, Command):
        current.update({k: v for k, v in result.update.items() if k != "messages"})


def strategies(service):
    from src.strategy.general.strategy import GeneralStrategy
    from src.strategy.merchant.strategy import MerchantStrategy
    from src.strategy.general.context.middleware import PageInputMiddleware
    from src.strategy.general.reliability import DeviceOperationReliabilityMiddleware
    from src.strategy.general.captcha import CaptchaHumanInTheLoopMiddleware
    from src.strategy.merchant.persistence import CollectionPersistenceMiddleware
    from src.device import DeviceOperationService

    controller = page_controller(service.mechanism)
    general = GeneralStrategy(
        controller,
        PageInputMiddleware(mechanism=service.mechanism),
        DeviceOperationReliabilityMiddleware(event_hub=DeviceOperationService()),
        CaptchaHumanInTheLoopMiddleware(),
    )
    return general, MerchantStrategy(general, CollectionPersistenceMiddleware(service))
