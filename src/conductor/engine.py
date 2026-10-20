"""LangChain create_agent based agent harness.

This module intentionally does not implement the model/tool run loop itself.
LangChain's ``create_agent`` owns that loop through its graph runtime.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import Final

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool

from src.agent_messages.text import latest_message_text, message_content
from src.agent_messages.types import GraphInput, InputMessage
from src.agent_runtime.conversation import MessageBlock
from src.agent_runtime.engine import AgentEngine
from src.agent_runtime.strategy.base import Strategy
from src.agent_runtime.strategy.registration import StrategyRegistration
from src.agent_runtime.types.execution import EngineResult, EngineStatus
from src.agent_runtime.types.framework import RuntimeMiddleware
from src.agent_runtime.types.response import AgentResponse
from src.common.utils.json import JsonObject, JsonValue
from src.conductor.state import (
    PROBE_COMPLETED,
    PROBE_INACTIVE,
    ConductorInput,
    ConductorState,
    initial_probe_state,
    read_conductor_fields,
)
from src.device import DeviceOperationService
from src.middleware.execution.policy import ToolExecutionPolicyMiddleware
from src.middleware.probe.lifecycle import ProbeLifecycleMiddleware
from src.page.mechanism import PageMechanism
from src.prompts import CONDUCTOR_PROMPT, build_initial_messages, build_probe_task_spec
from src.strategy.general.captcha import create_captcha_human_in_the_loop_middleware
from src.strategy.general.context.middleware import PageInputMiddleware
from src.strategy.general.reliability import DeviceOperationReliabilityMiddleware
from src.strategy.general.state import GENERAL, GeneralState, PageVisit, initial_general_state
from src.strategy.general.strategy import GeneralStrategy
from src.strategy.general.tools import create_confirm_page_tool
from src.strategy.general.visits import PageVisitController
from src.strategy.merchant.persistence import CollectionPersistenceMiddleware
from src.strategy.merchant.state import MERCHANT, MerchantState, initial_merchant_state
from src.strategy.merchant.strategy import MerchantStrategy
from src.tools.captcha.tools import CaptchaAuthenticationTool, create_captcha_tools
from src.tools.collection.continuous import ContinuousCollection
from src.tools.collection.service import CollectionService
from src.tools.communication.tools import create_talk_tool
from src.tools.device.navigation import create_navigation_tools
from src.tools.device.operations import create_conductor_event_hub_tools
from src.tools.device.pages import create_page_tools
from src.tools.notifications.service import OperationNoticeTool
from src.tools.page_notes.tools import create_page_notes_tool
from src.tools.reasoning.tools import create_thinking_tool
from src.tools.workbooks.excel import CollectionWorkbookSink

APP_PROBE_TRIGGER: Final[str] = "执行应用探测任务"


def messages_request_app_probe(messages: Sequence[InputMessage]) -> bool:
    """Recognize the conductor's exact application-probe trigger."""

    for message in messages:
        content: object = message.get("content") if isinstance(message, Mapping) else message_content(message)
        if isinstance(content, str) and content == APP_PROBE_TRIGGER:
            return True
    return False


def build_app_probe_messages() -> list[Mapping[str, str]]:
    """Build the conductor's application-probe task context."""

    return [{"role": "user", "content": content} for content in build_initial_messages()]


class ConductorEngine(AgentEngine[ConductorState]):
    """Translate probe requests without coupling the generic runtime to devices."""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        gateway_id: str,
        max_iterations: int = 1000,
        session_id: str | None = None,
        notifier: Callable[[str], None] | None = None,
    ) -> None:
        self._model: str | BaseChatModel = model
        self._notifier: Callable[[str], None] = notifier or self._notify
        self._business_middlewares: list[RuntimeMiddleware] = []
        super().__init__(
            model=model,
            gateway_id=gateway_id,
            max_iterations=max_iterations,
            session_id=session_id,
        )

    def _name(self) -> str:
        return "conductor"

    def _system_prompt(self) -> str:
        return CONDUCTOR_PROMPT

    def _create_middlewares(self) -> Sequence[RuntimeMiddleware]:
        return self._business_middlewares

    def _evaluate_result(self, state: Mapping[str, object]) -> EngineResult:
        response: AgentResponse | None = AgentResponse.parse(latest_message_text(state))
        if response is None:
            self._execution.continuation_reason = "structured_response_required"
        elif not response.resolved:
            self._execution.continuation_reason = "agent_reported_unresolved"
        elif not self._completion_allowed(state):
            self._execution.continuation_reason = "task_state_is_not_complete"
        else:
            return EngineResult(
                EngineStatus.COMPLETED,
                json.dumps(response.as_dict(), ensure_ascii=False, sort_keys=True),
            )
        return EngineResult(EngineStatus.CONTINUE_REQUIRED)

    def _prepare_messages(self, turn_messages: list[InputMessage]) -> list[InputMessage]:
        if self._requests_app_probe(turn_messages):
            return [*(dict(message) for message in build_app_probe_messages()), *turn_messages]
        return turn_messages

    def _input_state(self, messages: list[InputMessage]) -> GraphInput:
        if self._requests_app_probe(messages):
            state: ConductorInput = {
                "general": initial_general_state(),
                "merchant": initial_merchant_state(),
                "probe": {
                    **initial_probe_state(build_probe_task_spec().target_leaf_paths).get("probe", {}),
                    "package_name": build_probe_task_spec().package_name,
                },
                "messages": messages,
            }
            return state
        state = {"messages": messages, "probe": read_conductor_fields(self._state)["probe"]}
        return state

    def _requests_app_probe(self, messages: Sequence[InputMessage]) -> bool:
        if messages_request_app_probe(messages):
            return True
        block: MessageBlock | None = self.conversation.pending_block
        if block is None or APP_PROBE_TRIGGER not in block.source_texts:
            return False
        # Match original input only for this batch, never parse rendered dialogue
        # or reuse an unacknowledged command for an unrelated turn.
        for message in messages:
            identity: object = message.id if isinstance(message, BaseMessage) else message.get("id")
            if identity == block.block_id:
                return True
        return False

    def _completion_allowed(self, state: Mapping[str, object]) -> bool:
        return read_conductor_fields(state)["probe"].get("status") in (None, PROBE_INACTIVE, PROBE_COMPLETED)

    def _resume_payload(self, kind: str, user_input: JsonValue) -> JsonObject:
        message: str = "Human captcha authentication completed." if user_input is None else str(user_input)
        if kind == "liveness":
            return {"action": "continue", "message": message}
        return {"decisions": [{"type": "respond", "message": message}]}

    def _create_tools(self) -> Sequence[BaseTool]:
        operation_notice_service: OperationNoticeTool = OperationNoticeTool(notifier=self._notifier)
        event_hub: DeviceOperationService = DeviceOperationService()
        captcha: CaptchaAuthenticationTool = CaptchaAuthenticationTool(
            notifier=self._notifier, device_operations=event_hub
        )
        mechanism: PageMechanism = PageMechanism()
        from src.collector.core import CollectorTrainingCoordinator, create_prepare_collector_tool

        collector_manager: CollectorTrainingCoordinator = CollectorTrainingCoordinator(
            model=self._model,
            mechanism=mechanism,
            event_hub=event_hub,
            gateway_id=self.gateway_id,
        )
        collection_service: CollectionService = CollectionService(
            mechanism, collector_manager.training_registry, CollectionWorkbookSink(), event_hub
        )
        collection_persistence: CollectionPersistenceMiddleware = CollectionPersistenceMiddleware(
            collection_service
        )
        continuous_collection: ContinuousCollection = ContinuousCollection(
            collection_service, event_hub, self._tool_yield_reason
        )
        lifecycle: ProbeLifecycleMiddleware = ProbeLifecycleMiddleware(
            mechanism=mechanism,
        )
        visits: PageVisitController = PageVisitController(
            mechanism,
            strategy_names=frozenset({GENERAL, MERCHANT}),
            begin_merchant=MerchantStrategy.begin_visit,
            end_merchant=MerchantStrategy.end_visit,
        )
        visits.validate_targets(build_probe_task_spec().target_leaf_paths)
        registered_tools: list[BaseTool] = [
            create_confirm_page_tool(visits),
            *create_captcha_tools(captcha),
            create_thinking_tool(),
            create_talk_tool(self.send_message),
            create_page_notes_tool(mechanism),
            *create_conductor_event_hub_tools(event_hub),
            *create_page_tools(event_hub.page_bridge),
            *create_navigation_tools(event_hub=event_hub),
            *create_prepare_collector_tool(collector_manager),
            *collection_persistence.create_tools(),
            continuous_collection.create_tool(),
            *lifecycle.create_tools(),
        ]
        general: GeneralStrategy = GeneralStrategy(
            visits,
            PageInputMiddleware(mechanism=mechanism),
            DeviceOperationReliabilityMiddleware(event_hub=event_hub),
            create_captcha_human_in_the_loop_middleware(registered_tools),
        )
        self._page_strategies: dict[str, Strategy[ConductorState]] = {
            GENERAL: general,
            MERCHANT: MerchantStrategy(general, collection_persistence),
        }
        self._business_middlewares = [
            lifecycle,
            ToolExecutionPolicyMiddleware(notice_service=operation_notice_service),
        ]

        return registered_tools

    def _strategy_middleware_position(self) -> int:
        # Lifecycle sees final strategy results; execution notices occur after admission.
        return 1

    def _strategy_registrations(self) -> Sequence[StrategyRegistration[ConductorState]]:
        return (
            StrategyRegistration(
                GENERAL, self._page_strategies[GENERAL], GeneralState, initial_general_state
            ),
            StrategyRegistration(
                MERCHANT, self._page_strategies[MERCHANT], MerchantState, initial_merchant_state
            ),
        )

    def _strategy_state_schema(self) -> type[ConductorState]:
        return ConductorState

    def _select_strategy(self, state: ConductorState) -> str:
        visit: PageVisit | None = state["general"].get("visit")
        return visit["strategy"] if visit is not None else GENERAL

    def _notify(self, message: str) -> None:
        if not self.send_message(message):
            raise RuntimeError("Agent outgoing message queue rejected the notification")
