"""Collector page-probing manager and tools."""

from __future__ import annotations

import json
from uuid import uuid4
from xml.sax.saxutils import escape

from langchain.tools import ToolRuntime
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import StructuredTool

from src.agent_runtime.contracts import AgentHandle
from src.collector.history import CollectorHistory
from src.collector.toolbox import CollectorToolbox
from src.collector.training import CollectorTrainingRegistry
from src.common.utils.json import JsonObject, JsonValue, load_json
from src.conductor.state import ConductorState, current_visit
from src.device import DeviceOperationService
from src.page.mechanism import PageMechanism
from src.strategy.general.declarations import requires_collection
from src.strategy.general.state import PageVisit
from src.strategy.merchant.state import is_merchant_visit
from src.tools.notifications.policy import (
    EXTERNAL_OPERATION_POLICY_METADATA_KEY,
    ExternalOperationPolicy,
)
from src.tools.shared.factory import create_tool

from .engine import CollectorEngine


class CollectorTrainingCoordinator:
    """Run an isolated collector agent for one page mechanism."""

    def __init__(
        self,
        *,
        model: str | BaseChatModel,
        mechanism: PageMechanism | None = None,
        event_hub: DeviceOperationService | None = None,
        handle: AgentHandle,
    ) -> None:
        self._handle = handle
        self.model: str | BaseChatModel = model
        self.mechanism: PageMechanism = mechanism if mechanism is not None else PageMechanism()
        self.event_hub: DeviceOperationService = (
            event_hub if event_hub is not None else DeviceOperationService()
        )
        self.training_registry: CollectorTrainingRegistry = CollectorTrainingRegistry()
        self.history: CollectorHistory = CollectorHistory()
        self.max_iterations: int = 1000

    def prepare_collector(
        self,
        page_path: str,
        page_description: str,
        merchant_schema: str,
        collection_schema: str,
        runtime: ToolRuntime[None, ConductorState],
    ) -> str:
        """Delegate run.agent.py preparation to an isolated collector."""

        runtime_state: ConductorState = runtime.state
        current_path: str = current_visit(runtime_state).get("path", "")
        visit_id: str = current_visit(runtime_state).get("visit_id", "")
        run_id: str = runtime_state["probe"].get("run_id", "")
        canonical_path: str = self.mechanism.validate_page_manual(page_path)
        if canonical_path != current_path:
            raise ValueError(
                f"prepare_collector page_path {canonical_path!r} does not match current page {current_path!r}."
            )
        if not visit_id:
            raise ValueError("prepare_collector requires the current page_visit_id.")
        if not run_id:
            raise ValueError("prepare_collector requires the current probe_run_id.")
        self.training_registry.register_visit(visit_id, canonical_path)
        self.training_registry.mark_untrained(visit_id, canonical_path)

        parsed_collection_schema: JsonObject = _require_json_object(collection_schema, "collection_schema")
        visit: PageVisit | None = runtime_state["general"].get("visit")
        toolbox: CollectorToolbox = CollectorToolbox(
            mechanism=self.mechanism,
            event_hub=self.event_hub,
            page_path=canonical_path,
            merchant_schema=merchant_schema,
            collection_schema=parsed_collection_schema,
            requires_merchant_profile=(
                is_merchant_visit(visit)
                if visit is not None
                else requires_collection(self.mechanism, canonical_path)
            ),
            history=self.history,
            page_visit_id=visit_id,
            run_id=run_id,
        )
        message: str = "\n".join(
            [
                "<collector_assignment>",
                f"<page_path>{escape(canonical_path)}</page_path>",
                f"<page_visit_id>{escape(visit_id)}</page_visit_id>",
                f"<page_description>{escape(page_description)}</page_description>",
                f"<merchant_schema>{escape(merchant_schema)}</merchant_schema>",
                f"<collection_schema>{escape(collection_schema)}</collection_schema>",
                "<workflow>Follow the visual-validation workflow in your system prompt. Read or create COLLECT.md, annotate regions, test run(view), save independent screenshot baselines, and regress every saved case for this page in the current probe run only. Restore the live page after all cases pass.</workflow>",
                "</collector_assignment>",
            ]
        )
        with self._handle.spawner.child() as handle:
            engine = CollectorEngine(
                model=self.model,
                toolbox=toolbox,
                handle=handle,
                registry=self.training_registry,
                session_id=f"collector:{visit_id}",
                max_iterations=self.max_iterations,
            )
            self._handle.spawner.start(engine)
        child_id = handle.agent_id
        task_id = self._handle.mailbox.call(child_id, message)
        return json.dumps(
            {
                "ok": True,
                "status": "submitted",
                "subagent_id": child_id,
                "task_id": task_id,
                "page_path": canonical_path,
                "page_visit_id": visit_id,
            },
            ensure_ascii=False,
        )


def create_collector_tools(
    *,
    mechanism: PageMechanism,
    event_hub: DeviceOperationService,
    page_path: str,
    merchant_schema: str = "{}",
    collection_schema: str = "{}",
    requires_merchant_profile: bool = False,
) -> list[StructuredTool]:
    return CollectorToolbox(
        mechanism=mechanism,
        event_hub=event_hub,
        page_path=page_path,
        run_id=uuid4().hex,
        merchant_schema=merchant_schema,
        collection_schema=collection_schema,
        requires_merchant_profile=requires_merchant_profile,
    ).create_tools()


def create_prepare_collector_tool(manager: CollectorTrainingCoordinator) -> list[StructuredTool]:
    return [
        create_tool(
            func=manager.prepare_collector,
            name="prepare_collector",
            description=(
                "Spawn a persistent collector worker and submit current-page training. "
                "Returns task_id, not a training result. Coordinate shared-page changes with the collector; "
                "use call for follow-up tasks and kill when this page's worker is no longer needed. "
                "Inputs: page_path, page_description, merchant_schema, collection_schema. "
                "Do not include task.md operation steps in page_description."
            ),
            metadata={
                EXTERNAL_OPERATION_POLICY_METADATA_KEY: ExternalOperationPolicy(
                    "Begin probing the current merchant."
                )
            },
        )
    ]


def _require_json_object(value: str, field_name: str) -> JsonObject:
    parsed: JsonValue = load_json(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    return parsed
