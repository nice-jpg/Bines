"""Collector page-probing manager and tools."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any, Callable, Sequence

from nTrace import NTrace, createNTraceEndMiddleware, createNTraceStartMiddleware

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool

try:
    from src.collector.coder import CollectorCoderSession
    from src.codex.model_retry import create_codex_model_retry_middleware
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism, eval_collection
    from src.prompts import COLLECTOR_PROMPT
    from src.tools.event_hub import EventHub
    from src.tools.operation_policy import (
        EXTERNAL_OPERATION_POLICY_METADATA_KEY,
        ExternalOperationPolicy,
    )
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from collector.coder import CollectorCoderSession
    from codex.model_retry import create_codex_model_retry_middleware
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
        coder_create_agent_factory: Callable[..., Any] | None = None,
        max_iterations: int = 80,
    ) -> None:
        self.model = model
        self.mechanism = mechanism or PageMechanism()
        self.event_hub = event_hub or EventHub()
        self.training_registry = CollectorTrainingRegistry()
        self.create_agent_factory = create_agent_factory
        self.coder_create_agent_factory = coder_create_agent_factory
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

        parsed_collection_schema = _require_json_object(collection_schema, "collection_schema")
        coder_session = CollectorCoderSession(
            model=self.model,
            mechanism=self.mechanism,
            page_path=canonical_path,
            page_visit_id=visit_id,
            page_description=page_description,
            collection_schema=parsed_collection_schema,
            create_agent_factory=self.coder_create_agent_factory,
        )
        toolbox = CollectorToolbox(
            mechanism=self.mechanism,
            event_hub=self.event_hub,
            page_path=canonical_path,
            merchant_schema=merchant_schema,
            collection_schema=parsed_collection_schema,
            requires_merchant_profile=self.mechanism.page_policy(canonical_path).role == "merchant",
            coder_session=coder_session,
        )
        agent = self._build_agent(toolbox)
        agent._ntrace.update(session_id=f"collector:{visit_id}")
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
                "3. Record at least two materially different XML/r1 pairs with save_baseline before requesting code.",
                "4. Call sniff_run first to evaluate the existing run.agent.py against every recorded pair.",
                "5. If the batch score is already 100, do not call coder. Otherwise call consult_coder; coder alone reads and modifies run.agent.py.",
                "6. Call sniff_run after each coder change, then return failed batch evaluation to the same coder until score is 100.",
                "7. Add further distinct state pairs when needed to improve same-class compatibility.",
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

        trace = NTrace(agent_name="collector", system_prompt=COLLECTOR_PROMPT)
        middleware = [
            createNTraceStartMiddleware(trace),
            *create_codex_model_retry_middleware(self.model),
            createNTraceEndMiddleware(trace),
        ]
        agent = create_agent(
            model=self.model,
            tools=toolbox.create_tools(),
            system_prompt=COLLECTOR_PROMPT,
            name="collector",
            middleware=middleware,
        )
        agent._ntrace = trace
        return agent


class CollectorToolbox:
    """Page-scoped tool implementations exposed only to collector."""

    REQUIRED_TRAINING_CASES = 2
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
        coder_session: CollectorCoderSession | None = None,
    ) -> None:
        self.mechanism = mechanism
        self.event_hub = event_hub
        self.page_path = page_path
        self.merchant_schema = _require_json_object(merchant_schema, "merchant_schema")
        self.collection_schema = _require_json_object(collection_schema, "collection_schema")
        self.requires_merchant_profile = requires_merchant_profile
        self.coder_session = coder_session
        self._merchant_profile: dict[str, Any] = {}
        self._training_cases: dict[str, dict[str, Any]] = {}
        self._training_case_revision = 0
        self._last_xml: str | None = None
        self._state_revision = 0
        self._passing_states: dict[str, dict[str, int]] = {}
        self._last_evaluation: dict[str, Any] = {
            "score": 0,
            "errors": ["not_evaluated"],
            "missing": ["result"],
        }
        self._last_sniff_script_fingerprint: str | None = None
        self._last_sniff_training_case_revision = -1
        self._last_sniff_valid = False
        self._last_sniff_state_fingerprint = ""
        self._last_batch_evaluation: dict[str, Any] = {
            "score": 0,
            "errors": ["not_evaluated"],
            "missing": ["result"],
            "cases": [],
        }
        self._reset_tap_attempted = False
        self._reset_tap_succeeded = False
        self._reset_tap_error: Any = None

    def collector_uiautomate(self) -> Any:
        return self._capture_xml(self.event_hub.uiautomate())

    def collector_swipe_up(self, x: int, y: int) -> Any:
        result = self._capture_xml(self.event_hub.swipe_up(x, y), after_swipe=True)
        return result

    def collector_swipe_up_long(self, x: int, y: int) -> Any:
        result = self._capture_xml(self.event_hub.swipe_up_long(x, y), after_swipe=True)
        return result

    def collector_swipe_to_random(self, x: int, y: int) -> Any:
        result = self._capture_xml(self.event_hub.swipe_to_random(x, y), after_swipe=True)
        return result

    def collector_swipe_down(self, x: int, y: int) -> Any:
        result = self._capture_xml(self.event_hub.swipe_down(x, y), after_swipe=True)
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
        if self._last_xml is None or self._state_revision != 0:
            return _json_result(
                {
                    "ok": False,
                    "saved": False,
                    "error_type": "MerchantProfileStateError",
                    "error": "Save the merchant profile from the initial top-state XML before any swipe.",
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

    def consult_coder(self, instruction: str = "") -> str:
        """Send accumulated evidence and the latest deterministic result to one coder session."""

        if len(self._training_cases) < self.REQUIRED_TRAINING_CASES:
            return _json_result(
                {
                    "ok": False,
                    "error_type": "CollectorTrainingCasesInsufficient",
                    "error": "Record at least two distinct XML/r1 pairs before consulting coder.",
                    "recorded_cases": len(self._training_cases),
                    "required_cases": self.REQUIRED_TRAINING_CASES,
                }
            )
        preflight: dict[str, Any] | None = None
        if not self._sniff_result_is_current():
            preflight = json.loads(self.sniff_run())
        if not self._last_sniff_valid:
            return _json_result(
                {
                    "ok": False,
                    "error_type": "CollectorSniffPreflightFailed",
                    "error": "The existing script could not be evaluated; coder was not called.",
                    "preflight": preflight,
                }
            )
        if self._last_batch_evaluation.get("score") == 100:
            return _json_result(
                {
                    "ok": True,
                    "skipped": True,
                    "reason": "existing_script_already_optimal",
                    "evaluation": self._last_batch_evaluation,
                    "verification": self.training_result(),
                }
            )
        if self.coder_session is None:
            return _json_result(
                {
                    "ok": False,
                    "error_type": "CollectorCoderUnavailable",
                    "error": "This toolbox was created without a collector-coder session.",
                }
            )
        return self.coder_session.implement(
            training_cases=list(self._training_cases.values()),
            evaluation=self._last_batch_evaluation,
            instruction=instruction,
        )

    def sniff_run(self) -> str:
        xml = self.collector_uiautomate()
        if not isinstance(xml, str):
            evaluation = {"score": 0, "errors": ["uiautomate_error"], "missing": ["result"]}
            self._record_evaluation(evaluation, self._script_fingerprint(), "", valid=False)
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
        if not self._training_cases:
            evaluation = {
                "score": 0,
                "errors": ["missing_baseline"],
                "missing": ["result"],
                "cases": [],
            }
            run_result: Any = None
        else:
            case_results: list[dict[str, Any]] = []
            for case in self._training_cases.values():
                case_run = json.loads(self.mechanism.sniff_run(self.page_path, str(case["xml"])))
                case_evaluation = eval_collection(case["r1"], case_run)
                passed = case_run.get("ok") is True and case_evaluation["score"] == 100
                if passed:
                    self._passing_states.setdefault(script_fingerprint, {})[
                        str(case["state_fingerprint"])
                    ] = int(case["state_revision"])
                case_results.append(
                    {
                        "case_id": case["case_id"],
                        "state_fingerprint": case["state_fingerprint"],
                        "state_revision": case["state_revision"],
                        "run": case_run,
                        "evaluation": case_evaluation,
                        "passed": passed,
                    }
                )
            evaluation = _batch_evaluation(case_results)
            current_case = self._training_cases.get(state_fingerprint)
            run_result = next(
                (item["run"] for item in case_results if item["case_id"] == current_case["case_id"]),
                None,
            ) if current_case is not None else None
        passed = evaluation["score"] == 100
        self._last_batch_evaluation = evaluation
        self._record_evaluation(evaluation, script_fingerprint, state_fingerprint, valid=True)
        return _json_result(
            {
                "ok": passed,
                "run": run_result,
                "evaluation": evaluation,
                "evaluated_cases": len(self._training_cases),
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
        state_fingerprint = _fingerprint(self._last_xml)
        state_revision = self._state_revision
        case_id = state_fingerprint
        self._training_cases[case_id] = {
            "case_id": case_id,
            "xml": self._last_xml,
            "r1": baseline,
            "state_fingerprint": state_fingerprint,
            "state_revision": state_revision,
        }
        self._training_case_revision += 1
        return _json_result(
            {
                "ok": True,
                "baseline_saved": True,
                "baseline": baseline,
                "state_fingerprint": state_fingerprint,
                "state_revision": state_revision,
                "training_case_count": len(self._training_cases),
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
                func=self.consult_coder,
                name="consult_coder",
                description=(
                    "Ask the persistent collector-coder to implement or repair run.agent.py. "
                    "The runtime first ensures the existing script has a current sniff_run result, skips coder when "
                    "the batch score is already 100, and otherwise supplies every recorded XML/r1 pair and failure."
                ),
            ),
            StructuredTool.from_function(
                func=self.sniff_run,
                name="sniff_run",
                description=(
                    "Capture unchanged current XML, run run.agent.py against every recorded XML/r1 pair, "
                    "and return deterministic batch evaluation plus multi-state verification status."
                ),
            ),
            StructuredTool.from_function(
                func=self.save_baseline,
                name="save_baseline",
                description=(
                    "Record baseline r1 with the most recently read XML as a persistent training pair. "
                    "A baseline for the same XML replaces that pair; distinct XML states accumulate."
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
        *,
        valid: bool,
    ) -> None:
        self._last_evaluation = evaluation
        self._last_sniff_script_fingerprint = script_fingerprint
        self._last_sniff_training_case_revision = self._training_case_revision
        self._last_sniff_valid = valid
        self._last_sniff_state_fingerprint = state_fingerprint

    def _sniff_result_is_current(self) -> bool:
        return bool(
            self._last_sniff_script_fingerprint == self._script_fingerprint()
            and self._last_sniff_training_case_revision == self._training_case_revision
        )


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


def _batch_evaluation(case_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not case_results:
        return {"score": 0, "errors": ["missing_baseline"], "missing": ["result"], "cases": []}
    errors: list[str] = []
    missing: list[str] = []
    scores: list[int] = []
    summaries: list[dict[str, Any]] = []
    for index, result in enumerate(case_results):
        evaluation = result["evaluation"]
        score = int(evaluation.get("score", 0))
        scores.append(score)
        errors.extend(f"cases[{index}].{path}" for path in evaluation.get("errors", []))
        missing.extend(f"cases[{index}].{path}" for path in evaluation.get("missing", []))
        summaries.append(
            {
                "case_id": result["case_id"],
                "state_fingerprint": result["state_fingerprint"],
                "state_revision": result["state_revision"],
                "score": score,
                "errors": list(evaluation.get("errors", [])),
                "missing": list(evaluation.get("missing", [])),
                "run": result["run"],
            }
        )
    return {
        "score": min(scores),
        "errors": errors,
        "missing": missing,
        "cases": summaries,
    }


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
