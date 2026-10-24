"""Explicit page fixtures and tool requests for the strategy workflow."""

from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import ToolMessage
from page_helpers import page_mechanism

from src.conductor.state import ConductorFields, initial_fields, read_conductor_state
from src.page.collection_contract import CollectionContract
from src.strategy.general.page_visit import PageVisitMiddleware
from src.strategy.general.state import PageVisit
from src.strategy.merchant.state import begin_merchant_visit


def page_fields(path: str, visit_id: str, strategy: str) -> ConductorFields:
    fields: ConductorFields = initial_fields()
    visit: PageVisit = {"path": path, "visit_id": visit_id, "strategy": strategy}
    fields["active_strategy"] = strategy
    fields["general"].update(active=True, app_root=path.split("/")[0], visit=visit)
    if strategy == "merchant":
        fields["merchant"] = begin_merchant_visit(visit)
    return fields


def page_controller(mechanism):
    return PageVisitMiddleware(mechanism, frozenset({"general", "merchant"}))


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
        kind = "merchant" if path in {"meituan/外卖/美食/商家", "meituan/美食/商家"} else "general"
        (directory / "PAGE.md").write_text(f"---\npage_strategy: {kind}\n---\n\nPage {path}")
        (directory / "task.md").write_text("Page task")
    result = page_mechanism(root)
    for path in ("meituan/外卖/美食/商家", "meituan/美食/商家"):
        result.save_collection_contract(
            path, CollectionContract.from_dict({"primary_key": ["name"], "required_fields": ["name"]})
        )
    return result


def state(path="meituan/外卖/美食", **extra):
    kind = "merchant" if path in {"meituan/外卖/美食/商家", "meituan/美食/商家"} else "general"
    return read_conductor_state(
        {
            **page_fields(path, "visit-current", kind),
            "messages": [],
            **extra,
        }
    )


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
    from src.device import DeviceOperationService
    from src.strategy.general.captcha import CaptchaHumanInTheLoopMiddleware
    from src.strategy.general.context.middleware import PageInputMiddleware
    from src.strategy.general.reliability import DeviceOperationReliabilityMiddleware
    from src.strategy.general.strategy import GeneralStrategy
    from src.strategy.merchant.persistence import CollectionPersistenceMiddleware
    from src.strategy.merchant.strategy import MerchantStrategy

    controller = page_controller(service.mechanism)
    from src.device.services.execution import DeviceOperationExecutor
    from src.strategy.general.visit_history import VisitHistoryMiddleware
    from src.strategy.merchant.workflow import MerchantWorkflowMiddleware

    page = controller
    context = PageInputMiddleware(mechanism=service.mechanism)
    reliability = DeviceOperationReliabilityMiddleware(event_hub=DeviceOperationService())
    executor = DeviceOperationExecutor()
    captcha = CaptchaHumanInTheLoopMiddleware(authentication_tool=None, executor=executor)
    history = VisitHistoryMiddleware()
    return (
        GeneralStrategy(constraints=[captcha, page], features=[history, context, reliability]),
        MerchantStrategy(
            constraints=[captcha, MerchantWorkflowMiddleware(service), page],
            features=[history, context, CollectionPersistenceMiddleware(service), reliability],
        ),
    )
