"""Persistent collector-coder agent with file-only capabilities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from langchain_core.messages import BaseMessage
from langchain_core.tools import StructuredTool
from nTrace import NTrace, createNTraceEndMiddleware, createNTraceStartMiddleware

try:
    from src.codex.model_retry import create_codex_model_retry_middleware
    from src.page_mechanism import PageMechanism
    from src.prompts import COLLECTOR_CODER_PROMPT
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from codex.model_retry import create_codex_model_retry_middleware
    from page_mechanism import PageMechanism
    from prompts import COLLECTOR_CODER_PROMPT


class CollectorCoderToolbox:
    """The complete and intentionally narrow coder capability set."""

    def __init__(self, *, mechanism: PageMechanism, page_path: str) -> None:
        self.mechanism = mechanism
        self.page_path = page_path
        self._exposed_fingerprint: str | None = None

    def read_run_agent_py(self, force: bool = False) -> str:
        snapshot = self.mechanism.read_run_agent_py_snapshot(self.page_path)
        if snapshot["ok"] is not True:
            return _json_result(snapshot)
        content = str(snapshot["content"])
        fingerprint = _fingerprint(content)
        content_omitted = not force and fingerprint == self._exposed_fingerprint
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
            self._exposed_fingerprint = fingerprint
        return _json_result(result)

    def apply_run_agent_py_patch(self, patch: str) -> str:
        result = self.mechanism.apply_run_agent_py_patch(self.page_path, patch)
        payload = json.loads(result)
        if payload.get("ok") is True:
            self._exposed_fingerprint = str(payload["script_fingerprint"])
        return result

    def create_tools(self) -> list[StructuredTool]:
        return [
            StructuredTool.from_function(
                func=self.read_run_agent_py,
                name="read_run_agent_py",
                description=(
                    "Read the current page's run.agent.py. Repeated unchanged reads omit content. "
                    "Set force=true only when stagnation_review_required requests a full architecture audit."
                ),
            ),
            StructuredTool.from_function(
                func=self.apply_run_agent_py_patch,
                name="apply_run_agent_py_patch",
                description=(
                    "Incrementally update only run.agent.py with one Codex Update File patch. "
                    "The patch must satisfy the grammar and scope in the coder system prompt."
                ),
            ),
        ]


class CollectorCoderSession:
    """Keep one coder agent and explicit conversation history for a collector run."""

    STAGNATION_REVIEW_ATTEMPT = 3

    def __init__(
        self,
        *,
        model: Any,
        mechanism: PageMechanism,
        page_path: str,
        page_visit_id: str,
        page_description: str,
        collection_schema: Mapping[str, Any],
        create_agent_factory: Callable[..., Any] | None = None,
        max_iterations: int = 1000,
    ) -> None:
        self.model = model
        self.mechanism = mechanism
        self.page_path = page_path
        self.page_visit_id = page_visit_id
        self.page_description = page_description
        self.collection_schema = dict(collection_schema)
        self.create_agent_factory = create_agent_factory
        self.max_iterations = max(1, int(max_iterations))
        self.toolbox = CollectorCoderToolbox(mechanism=mechanism, page_path=page_path)
        self._agent: Any | None = None
        self._message_history: list[Any] = []
        self._sent_case_ids: set[str] = set()
        self._evaluation_scores: list[int] = []
        self._script_metrics_history: list[dict[str, int]] = []
        self._turn = 0

    def implement(
        self,
        *,
        training_cases: Sequence[Mapping[str, Any]],
        evaluation: Mapping[str, Any],
        instruction: str = "",
    ) -> str:
        if not training_cases:
            return _json_result(
                {
                    "ok": False,
                    "error_type": "CoderTrainingCasesMissing",
                    "error": "Record at least one XML/baseline pair before consulting coder.",
                }
            )
        agent = self._agent_instance()
        new_cases = [case for case in training_cases if str(case["case_id"]) not in self._sent_case_ids]
        current_score = _evaluation_score(evaluation)
        current_script = self.mechanism.read_run_agent_py(self.page_path)
        current_script_metrics = _script_metrics(current_script)
        optimization_attempt = self._turn + 1
        stagnation_review_required = bool(
            optimization_attempt >= self.STAGNATION_REVIEW_ATTEMPT
            and current_score < 100
        )
        payload = {
            "page_path": self.page_path,
            "template_parent_path": self.page_path.rpartition("/")[0] or self.page_path,
            "page_description": self.page_description if self._turn == 0 else None,
            "collection_schema": self.collection_schema if self._turn == 0 else None,
            "new_training_cases": new_cases,
            "known_training_case_count": len(training_cases),
            "latest_sniff_run": dict(evaluation),
            "problem_report": _build_problem_report(evaluation, instruction),
            "optimization_history": {
                "attempt": optimization_attempt,
                "prior_scores": list(self._evaluation_scores),
                "current_score": current_score,
                "stagnation_review_required": stagnation_review_required,
                "prior_script_metrics": list(self._script_metrics_history),
                "current_script_metrics": current_script_metrics,
            },
        }
        new_message = {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        request_messages = [*self._message_history, new_message]
        try:
            state = agent.invoke(
                {"messages": request_messages},
                config={
                    "recursion_limit": self.max_iterations,
                    "configurable": {"thread_id": f"collector-coder:{self.page_visit_id}"},
                },
            )
        except Exception as exc:  # noqa: BLE001 - collector needs a structured repair signal.
            return _json_result(
                {"ok": False, "error_type": type(exc).__name__, "error": str(exc), "turn": self._turn}
            )
        returned_messages = _state_messages(state)
        if not returned_messages:
            return _json_result(
                {
                    "ok": False,
                    "error_type": "CoderHistoryMissing",
                    "error": "Coder returned no message history; the coding turn was not committed.",
                    "turn": self._turn,
                }
            )
        self._message_history = _merge_returned_history(request_messages, returned_messages)
        self._sent_case_ids.update(str(case["case_id"]) for case in new_cases)
        self._evaluation_scores.append(current_score)
        self._script_metrics_history.append(current_script_metrics)
        self._turn += 1
        return _json_result(
            {
                "ok": True,
                "turn": self._turn,
                "new_training_cases": len(new_cases),
                "known_training_cases": len(self._sent_case_ids),
                "coder_message": _latest_text(state),
                "history_messages": len(self._message_history),
                "script_fingerprint": _fingerprint(self.mechanism.read_run_agent_py(self.page_path)),
            }
        )

    def _agent_instance(self) -> Any:
        if self._agent is not None:
            return self._agent
        create_agent = self.create_agent_factory
        if create_agent is None:
            from langchain.agents import create_agent as create_agent  # noqa: PLC0415
        trace = NTrace(agent_name="collector-coder", system_prompt=COLLECTOR_CODER_PROMPT)
        trace.update(session_id=f"collector-coder:{self.page_visit_id}")
        self._agent = create_agent(
            model=self.model,
            tools=self.toolbox.create_tools(),
            system_prompt=COLLECTOR_CODER_PROMPT,
            name="collector-coder",
            middleware=[
                createNTraceStartMiddleware(trace),
                *create_codex_model_retry_middleware(self.model),
                createNTraceEndMiddleware(trace),
            ],
        )
        return self._agent

    def message_history(self) -> tuple[Any, ...]:
        """Return a read-only snapshot of committed coder conversation messages."""

        return tuple(self._message_history)


def _latest_text(state: Mapping[str, Any]) -> str:
    messages = _state_messages(state)
    if not messages:
        return ""
    latest = messages[-1]
    content = latest.get("content") if isinstance(latest, Mapping) else latest.content
    return content if isinstance(content, str) else str(content or "")


def _state_messages(state: Mapping[str, Any]) -> list[Any]:
    messages = state.get("messages") or []
    if isinstance(messages, Sequence) and not isinstance(messages, (str, bytes)):
        return list(messages)
    return []


def _merge_returned_history(request_messages: Sequence[Any], returned_messages: Sequence[Any]) -> list[Any]:
    """Accept normal full-state responses while tolerating minimal test/custom agents."""

    if len(returned_messages) >= len(request_messages) and all(
        _message_identity(actual) == _message_identity(expected)
        for actual, expected in zip(returned_messages, request_messages)
    ):
        return list(returned_messages)
    return [*request_messages, *returned_messages]


def _message_identity(message: Any) -> tuple[str, str]:
    if isinstance(message, Mapping):
        role = str(message.get("role") or message.get("type") or "")
        content = message.get("content")
    elif isinstance(message, BaseMessage):
        role = message.type
        content = message.content
    else:
        return type(message).__name__, str(message)
    role = {"human": "user", "ai": "assistant"}.get(role, role)
    return role, content if isinstance(content, str) else str(content or "")


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _build_problem_report(
    evaluation: Mapping[str, Any],
    instruction: str,
) -> dict[str, Any]:
    cases = evaluation.get("cases")
    case_results = cases if isinstance(cases, Sequence) and not isinstance(cases, str) else []
    failing_cases = [
        {
            "case_id": case.get("case_id"),
            "score": case.get("score"),
            "errors": list(case.get("errors") or []),
            "missing": list(case.get("missing") or []),
        }
        for case in case_results
        if isinstance(case, Mapping) and int(case.get("score", 0)) < 100
    ]
    return {
        "structural_analysis": instruction.strip() or None,
        "score": evaluation.get("score", 0),
        "failed_paths": list(evaluation.get("errors") or []),
        "missing_paths": list(evaluation.get("missing") or []),
        "failing_cases": failing_cases,
        "analysis_scope": (
            "Infer a template-level structural exception shared by possible merchant states; "
            "do not turn literal sample text into extraction conditions."
        ),
    }


def _evaluation_score(evaluation: Mapping[str, Any]) -> int:
    value = evaluation.get("score", 0)
    if isinstance(value, bool):
        return 0
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0


def _script_metrics(content: str) -> dict[str, int]:
    return {
        "bytes": len(content.encode("utf-8")),
        "lines": len(content.splitlines()),
    }


def _json_result(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
