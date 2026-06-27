"""Single-invoke optimizer runtime and tool implementations."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .dumper import DumperConfig, dump_full_xml
from .middleware import OptimizerTrace
from .models import OptimizationRound, RecognizerResult, ScoreResult
from .optimizer_script import load_optimizer, read_optimizer_source, write_optimizer_source
from .provenance import commit_round
from .recognizer_agent import (
    HiddenSubtreeCandidate,
    analyze_hidden_subtrees,
    build_recognizer_agent,
    parse_recognizer_output,
    prepare_recognizer_xml,
    recognize_functions_locally,
)
from .scorer import score_regions


@dataclass(frozen=True)
class StoredXml:
    ref: str
    length: int


@dataclass(frozen=True)
class StoredRecognition:
    ref: str
    count: int
    ok: bool
    error: str | None


@dataclass(frozen=True)
class StoredScore:
    score_ref: str
    score: float
    fidelity: float
    compression: float
    missing_count: int
    execution_error: str | None
    hidden_pruning: float
    hidden_pruning_reward: float
    hidden_subtree_count: int
    hidden_candidate_count: int
    hidden_removed_count: int
    stop: bool


@dataclass
class OptimizerRuntime:
    """State owned by one main-agent invocation."""

    model: Any
    dumper_config: DumperConfig
    output: Path
    trace: OptimizerTrace
    max_rounds: int = 10
    min_growth: float = 1.0
    stale_rounds: int = 3

    def __post_init__(self) -> None:
        self.workspace_dir = self.output.parent
        self.xml_store: dict[str, str] = {}
        self.recognition_store: dict[str, RecognizerResult] = {}
        self.score_store: dict[str, ScoreResult] = {}
        self.optimizer_error_store: dict[str, dict[str, str]] = {}
        self.optimizer_error_score_refs: dict[str, str] = {}
        self.hidden_analysis_store: dict[str, list[HiddenSubtreeCandidate]] = {}
        self.subagents: dict[str, Any] = {}
        self._subagent_roles: dict[str, str] = {}
        self._next_subagent_id = 1
        self._next_xml_id = 0
        self._next_recognition_id = 0
        self._next_score_id = 0
        self.best_score = float("-inf")
        self.last_score: float | None = None
        self.stale_count = 0

    def build_tools(self) -> list[Any]:
        from langchain_core.tools import StructuredTool

        return [
            StructuredTool.from_function(self.dump_full_xml_tool, name="dump_full_xml"),
            StructuredTool.from_function(
                self.analyze_hidden_subtrees_tool,
                name="analyze_hidden_subtrees",
            ),
            StructuredTool.from_function(self.spawn_subagent_tool, name="spawn"),
            StructuredTool.from_function(self.call_subagent_tool, name="call"),
            StructuredTool.from_function(self.kill_subagent_tool, name="kill"),
            StructuredTool.from_function(self.get_optimizer_source_tool, name="get_optimizer_source"),
            StructuredTool.from_function(self.optimize_xml_tool, name="optimize_xml"),
            StructuredTool.from_function(self.score_round_tool, name="score_round"),
            StructuredTool.from_function(self.apply_optimizer_tool, name="apply_optimizer"),
            StructuredTool.from_function(self.should_stop_tool, name="should_stop"),
        ]

    def dump_full_xml_tool(self) -> str:
        """Capture full UI XML with trusted nice_dumper and store it as XML0."""

        xml = dump_full_xml(self.dumper_config)
        ref = "XML0"
        self.xml_store[ref] = xml
        return _json({"xml_ref": ref, "length": len(xml), "preview": xml[:500]})

    def spawn_subagent_tool(self, role: str) -> str:
        """Spawn a subagent. Supported role: recognizer."""

        normalized = str(role or "").strip().lower()
        if normalized != "recognizer":
            return _json({"error": f"unsupported subagent role: {role}"})
        subagent_id = f"subagent-{self._next_subagent_id}"
        self._next_subagent_id += 1
        self.subagents[subagent_id] = build_recognizer_agent(self.model)
        self._subagent_roles[subagent_id] = normalized
        return _json({"subagent_id": subagent_id, "role": normalized})

    def analyze_hidden_subtrees_tool(self, xml_ref: str = "XML0") -> str:
        """Find invisible or inactive preloaded subtrees worth pruning first."""

        xml = self.xml_store.get(xml_ref)
        if xml is None:
            return _json({"error": f"unknown xml_ref: {xml_ref}"})
        candidates = analyze_hidden_subtrees(xml)
        self.hidden_analysis_store[xml_ref] = candidates
        return _json(
            {
                "xml_ref": xml_ref,
                "candidate_count": len(candidates),
                "estimated_removable_characters": sum(
                    item.estimated_characters for item in candidates
                ),
                "candidates": [asdict(item) for item in candidates],
                "optimizer_guidance": (
                    "Prune each maximal candidate subtree before generic attribute "
                    "compression. Prefer explicit visible-to-user=false. For legacy "
                    "pull-loading candidates, require every reported signal together. "
                    "Do not infer occlusion from sibling order or bounds alone, and do "
                    "not remove the active overlapping sibling."
                ),
            }
        )

    def call_subagent_tool(self, subagent_id: str, xml_ref: str) -> str:
        """Call a spawned subagent with a stored XML reference."""

        if subagent_id not in self.subagents:
            return _json({"error": f"unknown subagent_id: {subagent_id}"})
        xml = self.xml_store.get(xml_ref)
        if xml is None:
            return _json({"error": f"unknown xml_ref: {xml_ref}"})
        if self._subagent_roles.get(subagent_id) != "recognizer":
            return _json({"error": f"unsupported subagent role for {subagent_id}"})
        result = self._call_recognizer_subagent(subagent_id, xml)
        self._next_recognition_id += 1
        ref = f"L{self._next_recognition_id - 1}"
        self.recognition_store[ref] = result
        return _json(
            {
                "recognition_ref": ref,
                "ok": result.ok,
                "error": result.error,
                "function_count": len(result.functions),
                "functions": [asdict(item) for item in result.functions],
            }
        )

    def kill_subagent_tool(self, subagent_id: str) -> str:
        """Kill a spawned subagent and release it from runtime state."""

        existed = subagent_id in self.subagents
        self.subagents.pop(subagent_id, None)
        self._subagent_roles.pop(subagent_id, None)
        return _json({"subagent_id": subagent_id, "killed": existed})

    def get_optimizer_source_tool(self) -> str:
        """Return current optimizer source for the main agent to improve."""

        source = read_optimizer_source(self.output)
        return _json({"optimizer_ref": "optimizer_source", "source": source})

    def optimize_xml_tool(self, xml_ref: str = "XML0") -> str:
        """Run current optimizer against a stored XML reference and store the result."""

        xml = self.xml_store.get(xml_ref)
        if xml is None:
            return _json({"error": f"unknown xml_ref: {xml_ref}"})

        try:
            optimizer = load_optimizer(self.output)
        except (Exception, SystemExit) as exc:
            return self._record_optimizer_error("load", exc)

        try:
            optimized = str(optimizer(xml))
        except (Exception, SystemExit) as exc:
            return self._record_optimizer_error("execute", exc)

        self._next_xml_id += 1
        out_ref = f"XML{self._next_xml_id}"
        self.xml_store[out_ref] = optimized
        return _json(
            {
                "ok": True,
                "xml_ref": out_ref,
                "length": len(optimized),
                "preview": optimized[:500],
            }
        )

    def score_round_tool(self, xml0_ref: str, xml1_ref: str, l0_ref: str, l1_ref: str) -> str:
        """Score one optimization round and store the score."""

        xml0 = self.xml_store.get(xml0_ref)
        xml1 = self.xml_store.get(xml1_ref)
        l0 = self.recognition_store.get(l0_ref)
        l1 = self.recognition_store.get(l1_ref)
        error_score_ref = self.optimizer_error_score_refs.get(xml1_ref)
        if error_score_ref is not None:
            score = self.score_store[error_score_ref]
            return _json(asdict(self._score_summary(error_score_ref, score)))
        if xml0 is None or xml1 is None or l0 is None or l1 is None:
            return _json({"error": "unknown score input reference"})
        hidden_candidates = self.hidden_analysis_store.get(xml0_ref)
        if hidden_candidates is None:
            hidden_candidates = analyze_hidden_subtrees(xml0)
            self.hidden_analysis_store[xml0_ref] = hidden_candidates
        score = score_regions(
            xml0,
            xml1,
            l0,
            l1,
            hidden_candidates=hidden_candidates,
        )
        ref = self._store_score(score)
        return _json(asdict(self._score_summary(ref, score)))

    def apply_optimizer_tool(self, score_ref: str, reason: str, script: str) -> str:
        """Apply a reasoned optimizer proposal and commit this round under workspace git."""

        score = self.score_store.get(score_ref)
        if score is None:
            return _json({"error": f"unknown score_ref: {score_ref}"})
        current_source = read_optimizer_source(self.output)
        proposed_source = str(script or "").strip()
        if proposed_source and proposed_source != current_source.strip():
            write_optimizer_source(self.output, proposed_source)
        else:
            proposed_source = current_source
        round_index = len(self.trace.rounds) + 1
        xml0 = self.xml_store.get("XML0", "")
        latest_xml_ref = f"XML{round_index}"
        xml1 = self.xml_store.get(latest_xml_ref, "")
        l0 = self.recognition_store.get("L0", RecognizerResult([]))
        l1 = self.recognition_store.get(f"L{round_index}", RecognizerResult([]))
        round_result = OptimizationRound(
            index=round_index,
            xml0_length=len(xml0),
            xml1_length=len(xml1),
            l0_count=len(l0.functions),
            l1_count=len(l1.functions),
            score=score,
            reason=str(reason or "").strip(),
            suggestion=proposed_source,
        )
        self.trace.append(round_result)
        growth = 0.0 if self.last_score is None else score.score - self.last_score
        if score.score > self.best_score:
            self.best_score = score.score
        if self.last_score is None:
            self.stale_count = 0
        elif 0 <= growth < self.min_growth:
            self.stale_count += 1
        elif growth >= self.min_growth:
            self.stale_count = 0
        self.last_score = score.score
        commit_round(workspace_dir=self.workspace_dir, optimizer_path=self.output, round_result=round_result)
        return _json(
            {
                "round": round_index,
                "score": score.score,
                "execution_error": score.execution_error,
                "growth": growth,
                "stale_count": self.stale_count,
                "stop": self._should_stop(),
                "reason": round_result.reason,
            }
        )

    def should_stop_tool(self) -> str:
        """Return whether the optimizer loop should stop."""

        return _json(
            {
                "stop": self._should_stop(),
                "rounds": len(self.trace.rounds),
                "max_rounds": self.max_rounds,
                "stale_count": self.stale_count,
                "stale_rounds": self.stale_rounds,
                "best_score": self.best_score,
                "last_score": self.last_score,
            }
        )

    def _call_recognizer_subagent(self, subagent_id: str, xml: str) -> RecognizerResult:
        agent = self.subagents[subagent_id]
        recognizer_xml = prepare_recognizer_xml(xml)
        prompt = "Analyze this XML and return JSON only:\n\n" + recognizer_xml
        last_output = ""
        last_error = ""
        for attempt in range(2):
            content = (
                prompt
                if attempt == 0
                else "Return valid JSON only for this XML:\n\n" + recognizer_xml
            )
            try:
                state = agent.invoke({"messages": [{"role": "user", "content": content}]})
                last_output = _latest_text(state)
                parsed = parse_recognizer_output(last_output)
                if parsed.ok:
                    return parsed
                last_error = str(parsed.error or "invalid recognizer output")
            except Exception as exc:  # noqa: BLE001 - fallback keeps the main workflow usable.
                last_error = f"{type(exc).__name__}: {exc}"
        fallback = recognize_functions_locally(recognizer_xml)
        if fallback.ok:
            return RecognizerResult(
                functions=fallback.functions,
                raw_output=f"local fallback used after subagent error: {last_error}",
            )
        return RecognizerResult(functions=[], raw_output=last_output, error=last_error or fallback.error)

    def _score_summary(self, ref: str, score: ScoreResult) -> StoredScore:
        return StoredScore(
            score_ref=ref,
            score=score.score,
            fidelity=score.fidelity,
            compression=score.compression,
            missing_count=score.missing_count,
            execution_error=score.execution_error,
            hidden_pruning=score.hidden_pruning,
            hidden_pruning_reward=score.hidden_pruning_reward,
            hidden_subtree_count=score.hidden_subtree_count,
            hidden_candidate_count=score.hidden_candidate_count,
            hidden_removed_count=score.hidden_removed_count,
            stop=self._should_stop(),
        )

    def _record_optimizer_error(self, stage: str, exc: BaseException) -> str:
        self._next_xml_id += 1
        xml_ref = f"XML{self._next_xml_id}"
        self.xml_store[xml_ref] = ""
        error_type = type(exc).__name__
        message = str(exc).strip() or repr(exc)
        error_text = f"{error_type}: {message}"
        self.optimizer_error_store[xml_ref] = {
            "stage": stage,
            "error_type": error_type,
            "message": message,
        }
        baseline = self.recognition_store.get("L0")
        missing_count = len(baseline.functions) if baseline is not None else 0
        score = ScoreResult(
            score=-1000.0,
            fidelity=0.0,
            compression=0.0,
            missing_count=missing_count,
            missing_penalty=1.0,
            matches=[],
            execution_error=error_text,
        )
        score_ref = self._store_score(score)
        self.optimizer_error_score_refs[xml_ref] = score_ref
        return _json(
            {
                "ok": False,
                "xml_ref": xml_ref,
                "score_ref": score_ref,
                "score": score.score,
                "stage": stage,
                "error_type": error_type,
                "error": message,
                "next_action": (
                    "Inspect the optimizer source, propose a corrected complete script, "
                    "and call apply_optimizer with this score_ref. "
                    "Do not call the recognizer for this failed XML."
                ),
            }
        )

    def _store_score(self, score: ScoreResult) -> str:
        self._next_score_id += 1
        ref = f"S{self._next_score_id}"
        self.score_store[ref] = score
        return ref

    def _should_stop(self) -> bool:
        return len(self.trace.rounds) >= self.max_rounds or self.stale_count >= self.stale_rounds


def _latest_text(state: dict[str, Any]) -> str:
    messages = state.get("messages") if isinstance(state, dict) else None
    if not messages:
        return ""
    latest = messages[-1]
    content = getattr(latest, "content", None)
    if content is None and isinstance(latest, dict):
        content = latest.get("content")
    return content if isinstance(content, str) else str(content or "")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)
