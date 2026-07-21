"""Collector agent manager and page-collection tools."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Mapping
from typing import Any, Callable, Sequence

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

try:
    from src.middleware.tool_io import tool_definition_name
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism, eval_collection
    from src.prompts import COLLECTOR_PROMPT
    from src.tools.event_hub import EventHub
    from src.tools.operation_policy import (
        EXTERNAL_OPERATION_POLICY_METADATA_KEY,
        ExternalOperationPolicy,
    )
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from middleware.tool_io import tool_definition_name
    from page_mechanism import CollectorTrainingRegistry, PageMechanism, eval_collection
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
        create_agent_factory: Callable[..., Any] | None = None,
        max_iterations: int = 80,
    ) -> None:
        self.model = model
        self.mechanism = mechanism or PageMechanism()
        self.event_hub = event_hub or EventHub()
        self.training_registry = CollectorTrainingRegistry()
        self.create_agent_factory = create_agent_factory
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

        toolbox = CollectorToolbox(
            mechanism=self.mechanism,
            event_hub=self.event_hub,
            page_path=canonical_path,
            merchant_schema=merchant_schema,
            collection_schema=collection_schema,
            requires_merchant_profile=self.mechanism.page_policy(canonical_path).role == "merchant",
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
                "<workflow>",
                "1. Call collector_uiautomate to inspect the real page XML.",
                "2. Before any swipe, extract the merchant profile from the top XML and call save_merchant_profile.",
                "3. Scroll only if needed to find a suitable same-page item collection region before baseline creation.",
                "4. Produce r1 matching collection_schema directly from the current XML and call save_baseline.",
                "5. Call sniff_run without changing page state to get r2 and deterministic evaluation.",
                "6. If the score is below 100, repair run.agent.py for the current XML before moving the page.",
                "7. After the state passes, swipe to a materially different state, save its baseline, and verify again.",
                "8. Continue until sniff_run.verification.ready is true for the same run.agent.py version.",
                "9. After verification is ready, inspect the current XML and use collector_tap on a visible "
                "same-page product-tab reset control when available.",
                "</workflow>",
                "</collector_assignment>",
            ]
        )
        state: Any = {"messages": []}
        invoke_error: Exception | None = None
        try:
            state = agent.invoke(
                {"messages": [{"role": "user", "content": message}]},
                config={"recursion_limit": self.max_iterations},
            )
        except Exception as exc:  # noqa: BLE001 - tool results should be structured.
            invoke_error = exc
        finally:
            restoration = toolbox.restore_to_top()

        collector_message = _latest_text(state)
        verification = toolbox.training_result()
        merchant_profile = toolbox.merchant_profile()
        trained = bool(invoke_error is None and verification["ready"] is True and restoration["restored"] is True)
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
        create_agent = self.create_agent_factory
        if create_agent is None:
            from langchain.agents import create_agent as create_agent  # noqa: PLC0415

        return create_agent(
            model=self.model,
            tools=toolbox.create_tools(),
            system_prompt=COLLECTOR_PROMPT,
            name="collector",
            middleware=[CollectorTapAvailabilityMiddleware(toolbox)],
        )


class CollectorTapAvailabilityMiddleware(AgentMiddleware):
    """Expose the reset tap only after merchant script verification is ready."""

    def __init__(self, toolbox: CollectorToolbox) -> None:
        self.toolbox = toolbox

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._prepare_model_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._prepare_model_request(request))

    def _prepare_model_request(self, request: ModelRequest) -> ModelRequest:
        if self.toolbox.reset_tap_available():
            return request
        return request.override(
            tools=[tool for tool in request.tools if _tool_name(tool) != "collector_tap"]
        )


class CollectorToolbox:
    """Page-scoped tool implementations exposed only to collector."""

    REQUIRED_VALIDATION_STATES = 2

    def __init__(
        self,
        *,
        mechanism: PageMechanism,
        event_hub: EventHub,
        page_path: str,
        merchant_schema: str = "{}",
        collection_schema: str = "{}",
        requires_merchant_profile: bool = False,
    ) -> None:
        self.mechanism = mechanism
        self.event_hub = event_hub
        self.page_path = page_path
        self.merchant_schema = _require_json_object(merchant_schema, "merchant_schema")
        self.collection_schema = _require_json_object(collection_schema, "collection_schema")
        self.requires_merchant_profile = requires_merchant_profile
        self._merchant_profile: dict[str, Any] = {}
        self._baseline: Any | None = None
        self._baseline_state_fingerprint = ""
        self._baseline_state_revision = -1
        self._last_xml: str | None = None
        self._state_revision = 0
        self._passing_states: dict[str, dict[str, int]] = {}
        self._last_evaluation: dict[str, Any] = {
            "score": 0,
            "errors": ["not_evaluated"],
            "missing": ["result"],
        }
        self._last_sniff_script_fingerprint: str | None = None
        self._last_sniff_state_fingerprint = ""
        self._exposed_script_fingerprint: str | None = None
        self._has_swiped = False
        self._reset_tap_attempted = False
        self._reset_tap_succeeded = False
        self._reset_tap_error: Any = None

    def collector_uiautomate(self) -> Any:
        return self._capture_xml(self.event_hub.uiautomate())

    def collector_swipe_up(self, x: int, y: int) -> Any:
        result = self._capture_xml(self.event_hub.swipe_up(x, y), after_swipe=True)
        if isinstance(result, str):
            self._has_swiped = True
        return result

    def collector_swipe_up_long(self, x: int, y: int) -> Any:
        result = self._capture_xml(self.event_hub.swipe_up_long(x, y), after_swipe=True)
        if isinstance(result, str):
            self._has_swiped = True
        return result

    def collector_swipe_to_random(self, x: int, y: int) -> Any:
        result = self._capture_xml(self.event_hub.swipe_to_random(x, y), after_swipe=True)
        if isinstance(result, str):
            self._has_swiped = True
        return result

    def collector_swipe_down(self, x: int, y: int) -> Any:
        result = self._capture_xml(self.event_hub.swipe_down(x, y), after_swipe=True)
        if isinstance(result, str):
            self._has_swiped = True
        return result

    def collector_tap(self, x: int, y: int) -> Any:
        """Tap a same-page reset control only after final verification passes."""

        if not self.requires_merchant_profile:
            return _json_result(
                {
                    "ok": False,
                    "error_type": "CollectorTapNotApplicable",
                    "error": "collector_tap is available only for merchant page restoration.",
                }
            )
        if self._reset_tap_attempted:
            return _json_result(
                {
                    "ok": False,
                    "error_type": "CollectorTapAlreadyUsed",
                    "error": "collector_tap can be called at most once per collector run.",
                }
            )
        verification = self.training_result()
        if verification["ready"] is not True:
            return _json_result(
                {
                    "ok": False,
                    "error_type": "CollectorTapLocked",
                    "error": "collector_tap is available only after verification.ready is true.",
                    "verification": verification,
                }
            )
        self._reset_tap_attempted = True
        result = self.event_hub.tap(x, y)
        if isinstance(result, str):
            self._reset_tap_succeeded = True
            self._reset_tap_error = None
            self._last_xml = result
        else:
            self._reset_tap_succeeded = False
            self._reset_tap_error = result
        return result

    def reset_tap_available(self) -> bool:
        return bool(
            self.requires_merchant_profile
            and not self._reset_tap_attempted
            and self.training_result()["ready"] is True
        )

    def save_merchant_profile(self, merchant: str) -> str:
        if self._last_xml is None:
            return _json_result(
                {
                    "ok": False,
                    "saved": False,
                    "error_type": "MerchantProfileStateError",
                    "error": "Read the initial top-state XML before saving the merchant profile.",
                }
            )
        if self._has_swiped:
            return _json_result(
                {
                    "ok": False,
                    "saved": False,
                    "error_type": "MerchantProfileStateError",
                    "error": "Merchant profile must be saved from the initial top state before any swipe.",
                }
            )
        try:
            profile = _coerce_json(merchant)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return _json_result(
                {"ok": False, "saved": False, "error_type": type(exc).__name__, "error": str(exc)}
            )
        errors = _schema_shape_errors(self.merchant_schema, profile, "merchant")
        if (
            self.requires_merchant_profile
            and isinstance(profile, Mapping)
            and not str(profile.get("name") or "").strip()
        ):
            errors.append("merchant.name")
        if errors:
            return _json_result({"ok": False, "saved": False, "errors": sorted(set(errors))})
        self._merchant_profile = dict(profile)
        return _json_result({"ok": True, "saved": True, "merchant_profile": self._merchant_profile})

    def merchant_profile(self) -> dict[str, Any]:
        return dict(self._merchant_profile)

    def read_run_agent_py(self) -> str:
        snapshot = self.mechanism.read_run_agent_py_snapshot(self.page_path)
        if snapshot["ok"] is not True:
            return _json_result(snapshot)
        content = str(snapshot["content"])
        fingerprint = _fingerprint(content)
        content_omitted = fingerprint == self._exposed_script_fingerprint
        result = {
            "ok": True,
            "canonical_path": snapshot["canonical_path"],
            "path": snapshot["path"],
            "script_fingerprint": fingerprint,
            "content_omitted": content_omitted,
            "bytes": len(content.encode("utf-8")),
            "lines": len(content.splitlines()),
        }
        if not content_omitted:
            result["content"] = content
            self._exposed_script_fingerprint = fingerprint
        return _json_result(result)

    def apply_run_agent_py_patch(self, patch: str) -> str:
        result = self.mechanism.apply_run_agent_py_patch(self.page_path, patch)
        payload = json.loads(result)
        if payload.get("ok") is True:
            self._exposed_script_fingerprint = str(payload["script_fingerprint"])
        return result

    def sniff_run(self) -> str:
        xml = self.collector_uiautomate()
        if not isinstance(xml, str):
            evaluation = {"score": 0, "errors": ["uiautomate_error"], "missing": ["result"]}
            self._record_evaluation(evaluation, self._script_fingerprint(), "")
            return _json_result(
                {
                    "ok": False,
                    "run": xml,
                    "evaluation": evaluation,
                    "verification": self.training_result(),
                }
            )

        state_fingerprint = _fingerprint(xml)
        script_fingerprint = self._script_fingerprint()
        run_result = json.loads(self.mechanism.sniff_run(self.page_path, xml))
        if self._baseline is None:
            evaluation = {
                "score": 0,
                "errors": ["missing_baseline"],
                "missing": ["result"],
            }
        elif self._baseline_state_revision != self._state_revision:
            evaluation = {
                "score": 0,
                "errors": ["baseline_state_mismatch"],
                "missing": ["result"],
            }
        else:
            evaluation = eval_collection(self._baseline, run_result)
        passed = run_result.get("ok") is True and evaluation["score"] == 100
        if passed:
            self._passing_states.setdefault(script_fingerprint, {})[state_fingerprint] = self._state_revision
        self._record_evaluation(evaluation, script_fingerprint, state_fingerprint)
        return _json_result(
            {
                "ok": passed,
                "run": run_result,
                "evaluation": evaluation,
                "verification": self.training_result(),
            },
        )

    def save_baseline(self, r1: str) -> str:
        if self._last_xml is None:
            return _json_result(
                {
                    "ok": False,
                    "baseline_saved": False,
                    "error": "Read the current XML before saving a baseline.",
                }
            )
        try:
            baseline = _coerce_json(r1)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            return _json_result(
                {
                    "ok": False,
                    "baseline_saved": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
        if not isinstance(baseline, dict):
            return _json_result(
                {
                    "ok": False,
                    "baseline_saved": False,
                    "error_type": "TypeError",
                    "error": "Baseline r1 must be a JSON object.",
                }
            )
        schema_errors = _schema_shape_errors(self.collection_schema, baseline, "result")
        if schema_errors:
            return _json_result(
                {"ok": False, "baseline_saved": False, "error_type": "SchemaError", "errors": schema_errors}
            )
        self._baseline = baseline
        self._baseline_state_fingerprint = _fingerprint(self._last_xml)
        self._baseline_state_revision = self._state_revision
        return _json_result(
            {
                "ok": True,
                "baseline_saved": True,
                "baseline": self._baseline,
                "state_fingerprint": self._baseline_state_fingerprint,
                "state_revision": self._state_revision,
            }
        )

    def training_result(self) -> dict[str, Any]:
        script_fingerprint = self._script_fingerprint()
        passing_states = self._passing_states.get(script_fingerprint, {})
        distinct_revisions = set(passing_states.values())
        last_result_is_current = self._last_sniff_script_fingerprint == script_fingerprint
        if self._last_sniff_script_fingerprint is None:
            effective_evaluation = self._last_evaluation
        elif last_result_is_current:
            effective_evaluation = self._last_evaluation
        else:
            effective_evaluation = {
                "score": 0,
                "errors": ["script_changed_since_evaluation"],
                "missing": [],
            }
        ready = bool(
            last_result_is_current
            and effective_evaluation.get("score") == 100
            and len(passing_states) >= self.REQUIRED_VALIDATION_STATES
            and len(distinct_revisions) >= self.REQUIRED_VALIDATION_STATES
            and (
                not self.requires_merchant_profile
                or bool(str(self._merchant_profile.get("name") or "").strip())
            )
        )
        return {
            "ready": ready,
            "score": effective_evaluation.get("score", 0),
            "evaluation": effective_evaluation,
            "validated_states": len(passing_states),
            "required_states": self.REQUIRED_VALIDATION_STATES,
            "validated_state_fingerprints": sorted(passing_states),
            "validated_state_revisions": sorted(distinct_revisions),
            "script_fingerprint": script_fingerprint,
            "current_state_fingerprint": self._last_sniff_state_fingerprint,
            "merchant_profile_saved": bool(str(self._merchant_profile.get("name") or "").strip()),
            "merchant_profile_required": self.requires_merchant_profile,
        }

    def restore_to_top(self) -> dict[str, Any]:
        result = {
            "restored": self._reset_tap_succeeded,
            "strategy": "reset_tap",
            "tap_attempted": self._reset_tap_attempted,
            "tap_succeeded": self._reset_tap_succeeded,
        }
        if not self._reset_tap_succeeded:
            result["error"] = (
                "reset_tap_not_executed"
                if not self._reset_tap_attempted
                else str(self._reset_tap_error)
            )
        return result

    def create_tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                func=self.collector_uiautomate,
                name="collector_uiautomate",
                description="Read the current UIAutomator XML.",
            ),
            StructuredTool.from_function(
                func=self.collector_swipe_up,
                name="collector_swipe_up",
                description="Scroll to a different state on the current page; cannot navigate into another page.",
            ),
            StructuredTool.from_function(
                func=self.collector_swipe_up_long,
                name="collector_swipe_up_long",
                description="Scroll upward a long distance to traverse a large collection region on the current page.",
            ),
            StructuredTool.from_function(
                func=self.collector_swipe_to_random,
                name="collector_swipe_to_random",
                description="Select a random same-page state for collector training and compatibility verification.",
            ),
            StructuredTool.from_function(
                func=self.collector_swipe_down,
                name="collector_swipe_down",
                description="Scroll to a different state on the current page; cannot navigate back.",
            ),
            StructuredTool.from_function(
                func=self.collector_tap,
                name="collector_tap",
                description=(
                    "On a merchant page after verification.ready is true, tap one visible same-page product-tab reset "
                    "control such as 点菜, 商品, or 全部商品 to return the merchant page to its top "
                    "product state. Locked before final verification; never use it for navigation."
                ),
            ),
            StructuredTool.from_function(
                func=self.read_run_agent_py,
                name="read_run_agent_py",
                description="Read the current page's run.agent.py.",
            ),
            StructuredTool.from_function(
                func=self.apply_run_agent_py_patch,
                name="apply_run_agent_py_patch",
                description=(
                    "Incrementally update run.agent.py with a patch matching the grammar in the collector system prompt. "
                    "It must contain exactly one update_hunk named run.agent.py and no add, delete, move, or other file."
                ),
            ),
            StructuredTool.from_function(
                func=self.sniff_run,
                name="sniff_run",
                description=(
                    "Capture unchanged current XML, run run.agent.py, compare against the baseline bound to that XML, "
                    "and return deterministic evaluation plus multi-state verification status."
                ),
            ),
            StructuredTool.from_function(
                func=self.save_baseline,
                name="save_baseline",
                description=(
                    "Store baseline r1 for the most recently read XML state. Replaces the prior baseline. "
                    "Input: r1 JSON string."
                ),
            ),
            StructuredTool.from_function(
                func=self.save_merchant_profile,
                name="save_merchant_profile",
                description=(
                    "Save the merchant profile from the initial top-of-page XML before any swipe. "
                    "Input: merchant JSON string matching merchant_schema with a non-empty name."
                ),
            ),
        ]

    def _capture_xml(self, result: Any, *, after_swipe: bool = False) -> Any:
        if isinstance(result, str):
            if after_swipe:
                self._state_revision += 1
            self._last_xml = result
        return result

    def _script_fingerprint(self) -> str:
        return _fingerprint(self.mechanism.read_run_agent_py(self.page_path))

    def _record_evaluation(
        self,
        evaluation: dict[str, Any],
        script_fingerprint: str,
        state_fingerprint: str,
    ) -> None:
        self._last_evaluation = evaluation
        self._last_sniff_script_fingerprint = script_fingerprint
        self._last_sniff_state_fingerprint = state_fingerprint


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


def _tool_name(tool: Any) -> str:
    return tool_definition_name(tool)


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


def _schema_shape_errors(schema: Any, value: Any, path: str) -> list[str]:
    errors: list[str] = []
    if isinstance(schema, Mapping):
        if not isinstance(value, Mapping):
            return [path]
        if not schema:
            return []
        for key, child_schema in schema.items():
            child_path = f"{path}.{key}"
            if key not in value:
                errors.append(child_path)
            else:
                errors.extend(_schema_shape_errors(child_schema, value[key], child_path))
        for key in set(value) - set(schema):
            errors.append(f"{path}.{key}")
    elif isinstance(schema, list):
        if not isinstance(value, list):
            return [path]
        if schema:
            for index, item in enumerate(value):
                errors.extend(_schema_shape_errors(schema[0], item, f"{path}[{index}]"))
    return errors


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
