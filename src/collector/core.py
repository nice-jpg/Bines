"""Collector page-probing manager and tools."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Sequence

from src.agent_runtime import create_runtime

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from .middleware import CollectorModelMiddleware

try:
    from src.collector.toolbox import CollectorToolbox
    from src.collector.history import CollectorHistory
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism
    from src.prompts import COLLECTOR_PROMPT
    from src.tools.event_hub import EventHub
    from src.tools.operation_policy import (
        EXTERNAL_OPERATION_POLICY_METADATA_KEY,
        ExternalOperationPolicy,
    )
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from collector.toolbox import CollectorToolbox
    from collector.history import CollectorHistory
    from page_mechanism import CollectorTrainingRegistry, PageMechanism
    from prompts import COLLECTOR_PROMPT
    from tools.event_hub import EventHub
    from tools.operation_policy import (
        EXTERNAL_OPERATION_POLICY_METADATA_KEY,
        ExternalOperationPolicy,
    )


class CollectorManager:
    """Run an isolated collector agent for one page mechanism."""

    def __init__(
        self,
        *,
        model: Any,
        mechanism: PageMechanism | None = None,
        event_hub: EventHub | None = None,
        max_iterations: int = 1000,
    ) -> None:
        self.model = model
        self.mechanism = mechanism or PageMechanism()
        self.event_hub = event_hub or EventHub()
        self.training_registry = CollectorTrainingRegistry()
        self.history = CollectorHistory()
        self.max_iterations = max(1, int(max_iterations))

    def prepare_collector(
        self,
        page_path: str,
        page_description: str,
        merchant_schema: str,
        collection_schema: str,
        runtime: ToolRuntime,
    ) -> str:
        """Delegate run.agent.py preparation to an isolated collector."""

        runtime_state = runtime.state
        current_path = str(runtime_state.get("page_context_path") or "")
        visit_id = str(runtime_state.get("page_visit_id") or "")
        canonical_path = self.mechanism.validate_page_manual(page_path)
        if canonical_path != current_path:
            raise ValueError(
                f"prepare_collector page_path {canonical_path!r} does not match current page {current_path!r}."
            )
        if not visit_id:
            raise ValueError("prepare_collector requires the current page_visit_id.")
        self.training_registry.register_visit(visit_id, canonical_path)
        self.training_registry.mark_untrained(visit_id, canonical_path)

        parsed_collection_schema = _require_json_object(collection_schema, "collection_schema")
        toolbox = CollectorToolbox(
            mechanism=self.mechanism,
            event_hub=self.event_hub,
            page_path=canonical_path,
            merchant_schema=merchant_schema,
            collection_schema=parsed_collection_schema,
            requires_merchant_profile=self.mechanism.page_policy(canonical_path).role == "merchant",
            history=self.history,
            page_visit_id=visit_id,
        )
        agent = self._build_agent(toolbox)
        message = "\n".join(
            [
                "<collector_assignment>",
                f"<page_path>{_escape_text(canonical_path)}</page_path>",
                f"<page_visit_id>{_escape_text(visit_id)}</page_visit_id>",
                f"<page_description>{_escape_text(page_description)}</page_description>",
                f"<merchant_schema>{_escape_text(merchant_schema)}</merchant_schema>",
                f"<collection_schema>{_escape_text(collection_schema)}</collection_schema>",
                "<workflow>Follow the visual-validation workflow in your system prompt. Read or create COLLECT.md, annotate regions, test run(view), save independent screenshot baselines, and regress every saved case. Restore the live page after all cases pass.</workflow>",
                "</collector_assignment>",
            ]
        )
        state: Any = {"messages": []}
        invoke_error: Exception | None = None
        try:
            state = agent.invoke(
                {"messages": [{"role": "user", "content": message}]},
                config={"recursion_limit": self.max_iterations, "configurable": {"thread_id": f"collector:{visit_id}"}},
            )
        except Exception as exc:  # noqa: BLE001 - tool results should be structured.
            invoke_error = exc
        finally:
            restoration = toolbox.restore_to_top()

        collector_message = _latest_text(state)
        verification = toolbox.training_result()
        merchant_profile = toolbox.merchant_profile()
        trained = bool(invoke_error is None and verification["ready"] is True and restoration["restored"] is True)
        if trained:
            try:
                toolbox.persist_history()
            except Exception as exc:
                invoke_error = exc
                trained = False
        registry_args = {
            "merchant_profile": merchant_profile,
            "script_fingerprint": verification["script_fingerprint"],
            "restoration": restoration,
        }
        if trained:
            self.training_registry.mark_trained(visit_id, canonical_path, **registry_args)
        else:
            self.training_registry.mark_untrained(visit_id, canonical_path, **registry_args)
        result = {
            "ok": invoke_error is None,
            "page_path": canonical_path,
            "page_visit_id": visit_id,
            "collector_message": collector_message,
            "merchant_profile": merchant_profile,
            "trained": trained,
            "verification": verification,
            "restoration": restoration,
        }
        if invoke_error is not None:
            result.update(
                {"error_type": type(invoke_error).__name__, "error": str(invoke_error)}
            )
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    def _build_agent(self, toolbox: CollectorToolbox) -> Any:
        return create_runtime(name="collector", model=self.model, tools=toolbox.create_tools(),
                              system_prompt=COLLECTOR_PROMPT,
                              middleware=[CollectorModelMiddleware()])


def create_collector_tools(
    *,
    mechanism: PageMechanism,
    event_hub: EventHub,
    page_path: str,
    merchant_schema: str = "{}",
    collection_schema: str = "{}",
    requires_merchant_profile: bool = False,
) -> list[StructuredTool]:
    return CollectorToolbox(
        mechanism=mechanism,
        event_hub=event_hub,
        page_path=page_path,
        merchant_schema=merchant_schema,
        collection_schema=collection_schema,
        requires_merchant_profile=requires_merchant_profile,
    ).create_tools()


def create_prepare_collector_tool(manager: CollectorManager) -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            func=manager.prepare_collector,
            name="prepare_collector",
            description=(
                "Prepare the collector script for the current page before conductor operations. "
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


def _latest_text(state: Mapping[str, Any]) -> str:
    messages: Sequence[Any] = state.get("messages") or []
    if not messages:
        return ""
    latest = messages[-1]
    content = latest.get("content") if isinstance(latest, Mapping) else latest.content
    return content if isinstance(content, str) else str(content or "")


def _escape_text(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _require_json_object(value: Any, field_name: str) -> dict[str, Any]:
    parsed = _coerce_json(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{field_name} must be a JSON object.")
    return parsed
