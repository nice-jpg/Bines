"""Collector agent manager and page-collection tools."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Sequence

from langchain_core.tools import StructuredTool

try:
    from src.page_mechanism import CollectorTrainingRegistry, PageMechanism, eval_collection
    from src.prompts import COLLECTOR_PROMPT
    from src.tools.event_hub import EventHub
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from page_mechanism import CollectorTrainingRegistry, PageMechanism, eval_collection
    from prompts import COLLECTOR_PROMPT
    from tools.event_hub import EventHub


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
        required_fields: str,
        output_schema: str,
    ) -> str:
        """Delegate run.py preparation to an isolated collector."""

        toolbox = CollectorToolbox(
            mechanism=self.mechanism,
            event_hub=self.event_hub,
            page_path=page_path,
            required_fields=required_fields,
            output_schema=output_schema,
        )
        agent = self._build_agent(toolbox)
        message = "\n".join(
            [
                "<collector_assignment>",
                f"<page_path>{_escape_text(page_path)}</page_path>",
                f"<page_description>{_escape_text(page_description)}</page_description>",
                f"<required_fields>{_escape_text(required_fields)}</required_fields>",
                f"<output_schema>{_escape_text(output_schema)}</output_schema>",
                "<workflow>",
                "1. Call collector_uiautomate to inspect the real page XML.",
                "2. Scroll only if needed to find a suitable same-page collection region before baseline creation.",
                "3. Produce r1 directly from the current XML as the baseline JSON.",
                "4. Call save_baseline with r1. A later save_baseline replaces the stored baseline.",
                "5. Call sniff_run without changing page state to get r2 and the internal evaluation result.",
                "6. If sniff_run.evaluation.score is below 100, analyze sniff_run.evaluation.errors and sniff_run.evaluation.missing, improve run.py with a general same-class-page heuristic, and repeat sniff_run on the same XML/page state.",
                "7. After the current state passes, swipe to a materially different state on the same page, inspect its XML, replace the baseline for that state, and call sniff_run again.",
                "8. Continue until sniff_run.verification.ready is true. This requires the same run.py version to pass at least two distinct swiped page states.",
                "</workflow>",
                "</collector_assignment>",
            ]
        )
        try:
            state = agent.invoke(
                {"messages": [{"role": "user", "content": message}]},
                config={"recursion_limit": self.max_iterations},
            )
        except Exception as exc:  # noqa: BLE001 - tool results should be structured.
            self.training_registry.mark_untrained(page_path)
            return json.dumps(
                {"ok": False, "error_type": type(exc).__name__, "error": str(exc), "trained": False},
                ensure_ascii=False,
                sort_keys=True,
            )
        collector_message = _latest_text(state)
        verification = toolbox.training_result()
        trained = verification["ready"] is True
        if trained:
            self.training_registry.mark_trained(page_path)
        else:
            self.training_registry.mark_untrained(page_path)
        return json.dumps(
            {
                "ok": True,
                "page_path": page_path,
                "collector_message": collector_message,
                "trained": trained,
                "verification": verification,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _build_agent(self, toolbox: CollectorToolbox) -> Any:
        create_agent = self.create_agent_factory
        if create_agent is None:
            from langchain.agents import create_agent as create_agent  # noqa: PLC0415

        return create_agent(
            model=self.model,
            tools=toolbox.create_tools(),
            system_prompt=COLLECTOR_PROMPT,
            name="collector",
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
        required_fields: str = "",
        output_schema: str = "",
    ) -> None:
        self.mechanism = mechanism
        self.event_hub = event_hub
        self.page_path = page_path
        self.required_fields = required_fields
        self.output_schema = output_schema
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

    def collector_uiautomate(self) -> Any:
        return self._capture_xml(self.event_hub.uiautomate())

    def collector_swipe_up(self, x: int, y: int) -> Any:
        return self._capture_xml(self.event_hub.swipe_up(x, y), after_swipe=True)

    def collector_swipe_down(self, x: int, y: int) -> Any:
        return self._capture_xml(self.event_hub.swipe_down(x, y), after_swipe=True)

    def read_run_py(self) -> str:
        return self.mechanism.read_run_py(self.page_path)

    def write_run_py(self, content: str) -> str:
        return self.mechanism.write_run_py(self.page_path, content)

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
            evaluation = eval_collection(self._baseline, run_result, self.required_fields, self.output_schema)
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
        }

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
                func=self.collector_swipe_down,
                name="collector_swipe_down",
                description="Scroll to a different state on the current page; cannot navigate back.",
            ),
            StructuredTool.from_function(
                func=self.read_run_py,
                name="read_run_py",
                description="Read the current page's run.py.",
            ),
            StructuredTool.from_function(
                func=self.write_run_py,
                name="write_run_py",
                description="Replace the current page's run.py. Input: content.",
            ),
            StructuredTool.from_function(
                func=self.sniff_run,
                name="sniff_run",
                description=(
                    "Capture unchanged current XML, run run.py, compare against the baseline bound to that XML, "
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
        ]

    def _capture_xml(self, result: Any, *, after_swipe: bool = False) -> Any:
        if isinstance(result, str):
            if after_swipe:
                self._state_revision += 1
            self._last_xml = result
        return result

    def _script_fingerprint(self) -> str:
        return _fingerprint(self.mechanism.read_run_py(self.page_path))

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
    required_fields: str = "",
    output_schema: str = "",
) -> list[StructuredTool]:
    return CollectorToolbox(
        mechanism=mechanism,
        event_hub=event_hub,
        page_path=page_path,
        required_fields=required_fields,
        output_schema=output_schema,
    ).create_tools()


def create_prepare_collector_tool(manager: CollectorManager) -> list[StructuredTool]:
    return [
        StructuredTool.from_function(
            func=manager.prepare_collector,
            name="prepare_collector",
            description=(
                "Prepare the collector script for the current page before conductor operations. "
                "Inputs: page_path, page_description, required_fields, output_schema. "
                "Do not include task.md operation steps in page_description."
            ),
        )
    ]


def _latest_text(state: Any) -> str:
    messages: Sequence[Any] = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", [])
    if not messages:
        return ""
    latest = messages[-1]
    content = latest.get("content") if isinstance(latest, dict) else getattr(latest, "content", None)
    return content if isinstance(content, str) else str(content or "")


def _escape_text(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
