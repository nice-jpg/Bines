"""Controlled slave execution and prompt revision runtime."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping

from .contracts import CognitiveSlave, SlaveDebugInfo
from .models import CognitiveRound, Evaluation, PromptChange
from .provenance import PromptCommit, commit_prompt_files


class CognitiveController:
    """State and tools owned by one master-agent invocation."""

    def __init__(
        self,
        *,
        slave: CognitiveSlave,
        debug_info: SlaveDebugInfo,
        max_rounds: int,
        target_score: int | None,
        stale_rounds: int,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be >= 1")
        if stale_rounds < 1:
            raise ValueError("stale_rounds must be >= 1")
        self.slave = slave
        self.debug_info = debug_info
        self.max_rounds = max_rounds
        self.target_score = target_score
        self.stale_rounds = stale_rounds
        self.prompt_paths = _resolve_prompt_paths(debug_info)
        self._results: dict[str, Any] = {}
        self._evaluated_runs: set[str] = set()
        self._snapshots: dict[int, dict[Path, bytes]] = {0: self._capture_prompts()}
        self._revision = 0
        self._next_revision = 0
        self.rounds: list[CognitiveRound] = []
        self.changes: list[PromptChange] = []
        self.best_score: int | None = None
        self.best_round = 0
        self.best_revision = 0
        self.prompt_commits: list[PromptCommit] = []
        self._pending_prompt_paths: set[Path] = set()
        self._pending_reasons: list[str] = []

    def build_tools(self) -> list[Any]:
        from langchain_core.tools import StructuredTool

        return [
            StructuredTool.from_function(self.inspect_slave_tool, name="inspect_slave"),
            StructuredTool.from_function(self.run_slave_tool, name="run_slave"),
            StructuredTool.from_function(self.eval_slave_tool, name="eval_slave"),
            StructuredTool.from_function(self.read_prompt_tool, name="read_prompt"),
            StructuredTool.from_function(self.write_prompt_tool, name="write_prompt"),
            StructuredTool.from_function(
                self.restore_best_prompts_tool,
                name="restore_best_prompts",
            ),
            StructuredTool.from_function(self.should_stop_tool, name="should_stop"),
        ]

    def inspect_slave_tool(self) -> str:
        """Return slave duties, goal, prompt layout, context, and editable paths."""

        return _json(
            {
                "responsibility": self.debug_info.responsibility,
                "expected_outcome": self.debug_info.expected_outcome,
                "prompt_structure": self.debug_info.prompt_structure,
                "prompt_paths": [str(path) for path in self.prompt_paths],
                "additional_context": _json_safe(self.debug_info.additional_context),
            }
        )

    def run_slave_tool(self) -> str:
        """Run one complete slave task and store its opaque result."""

        stop_reason = self._stop_reason()
        if stop_reason is not None:
            return _json({"error": f"controller has stopped: {stop_reason}"})
        if any(ref not in self._evaluated_runs for ref in self._results):
            return _json({"error": "evaluate the previous slave result before running again"})
        commits = self._commit_pending_prompt_changes(round_index=len(self.rounds) + 1)
        result = self.slave.run()
        run_ref = f"run-{len(self._results) + 1}"
        self._results[run_ref] = result
        return _json(
            {
                "run_ref": run_ref,
                "result": _json_safe(result),
                "prompt_commits": [asdict(item) for item in commits],
            }
        )

    def eval_slave_tool(self, run_ref: str) -> str:
        """Evaluate the exact stored result from a prior run_slave call."""

        if run_ref not in self._results:
            return _json({"error": f"unknown run_ref: {run_ref}"})
        if run_ref in self._evaluated_runs:
            return _json({"error": f"run already evaluated: {run_ref}"})
        evaluation = normalize_evaluation(self.slave.eval(self._results[run_ref]))
        self._evaluated_runs.add(run_ref)
        index = len(self.rounds) + 1
        score_ref = f"score-{index}"
        previous_evaluation = self.rounds[-1].score if self.rounds else None
        best_score_before_round = self.best_score
        current = self._capture_prompts()
        round_result = CognitiveRound(
            index=index,
            run_ref=run_ref,
            score_ref=score_ref,
            score=evaluation,
            prompt_revision=self._revision,
            prompt_hashes={str(path): _digest(data) for path, data in current.items()},
        )
        self.rounds.append(round_result)
        if self.best_score is None or evaluation.total > self.best_score:
            self.best_score = evaluation.total
            self.best_round = index
            self.best_revision = self._revision
        comparison = _evaluation_comparison(
            evaluation,
            previous=previous_evaluation,
            best_score_before_round=best_score_before_round,
        )
        return _json(
            {
                "score_ref": score_ref,
                "round": index,
                "total": evaluation.total,
                "dimensions": evaluation.dimensions,
                "details": evaluation.details,
                "comparison": comparison,
                "is_new_best": self.best_round == index,
                "best_score": self.best_score,
            }
        )

    def read_prompt_tool(self, path: str) -> str:
        """Read one declared prompt file."""

        resolved = self._allowed_path(path)
        return _json({"path": str(resolved), "content": resolved.read_text(encoding="utf-8")})

    def write_prompt_tool(self, path: str, content: str, reason: str) -> str:
        """Atomically replace one declared prompt file with complete UTF-8 content."""

        if not self.rounds:
            return _json({"error": "establish a scored baseline before editing prompts"})
        if any(ref not in self._evaluated_runs for ref in self._results):
            return _json({"error": "cannot edit prompts between run_slave and eval_slave"})
        resolved = self._allowed_path(path)
        before = resolved.read_bytes()
        after = str(content).encode("utf-8")
        if before == after:
            return _json({"error": "replacement content is unchanged"})
        _atomic_write(resolved, after)
        self._next_revision += 1
        self._revision = self._next_revision
        self._snapshots[self._revision] = self._capture_prompts()
        change = PromptChange(
            revision=self._revision,
            path=str(resolved),
            reason=str(reason or "").strip(),
            before_hash=_digest(before),
            after_hash=_digest(after),
        )
        self.changes.append(change)
        self._pending_prompt_paths.add(resolved)
        if change.reason:
            self._pending_reasons.append(change.reason)
        return _json(asdict(change))

    def restore_best_prompts_tool(self) -> str:
        """Restore the best revision only after an explicit stop condition."""

        stop_reason = self._stop_reason()
        if stop_reason is None:
            latest = self.rounds[-1].score if self.rounds else None
            return _json(
                {
                    "restored": False,
                    "error": (
                        "restore_best_prompts is finalization-only; analyze the "
                        "score change and test a repair while rounds remain"
                    ),
                    "latest_total": latest.total if latest else None,
                    "best_score": self.best_score,
                    "required_action": (
                        "compare result and dimension deltas, then revise wording, "
                        "order, document structure, or information timing"
                    ),
                }
            )
        snapshot = self._snapshots[self.best_revision]
        changed = self._restore_snapshot(snapshot)
        self._revision = self.best_revision
        commits = self._commit_restored_prompts(changed)
        return _json(
            {
                "restored": True,
                "best_round": self.best_round,
                "best_score": self.best_score,
                "best_revision": self.best_revision,
                "stop_reason": stop_reason,
                "changed_paths": changed,
                "prompt_commits": [asdict(item) for item in commits],
            }
        )

    def should_stop_tool(self) -> str:
        """Check round, target-score, and no-improvement termination conditions."""

        reason = self._stop_reason()
        return _json(
            {
                "stop": reason is not None,
                "reason": reason,
                "rounds": len(self.rounds),
                "best_round": self.best_round,
                "best_score": self.best_score,
            }
        )

    def restore_best_prompts(self) -> bool:
        """Idempotently restore best evaluated prompts after the agent exits."""

        snapshot = self._snapshots[self.best_revision]
        changed_paths = self._restore_snapshot(snapshot)
        self._revision = self.best_revision
        self._commit_restored_prompts(changed_paths)
        return bool(changed_paths)

    def _allowed_path(self, raw_path: str) -> Path:
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            base = self.debug_info.working_directory or Path.cwd()
            candidate = base / candidate
        resolved = candidate.resolve()
        if resolved not in self.prompt_paths:
            raise ValueError(f"path is not a declared prompt file: {raw_path}")
        return resolved

    def _capture_prompts(self) -> dict[Path, bytes]:
        return {path: path.read_bytes() for path in self.prompt_paths}

    def _commit_pending_prompt_changes(self, *, round_index: int) -> list[PromptCommit]:
        if not self._pending_prompt_paths:
            return []
        reasons = "; ".join(dict.fromkeys(self._pending_reasons))
        if len(reasons) > 500:
            reasons = reasons[:497] + "..."
        message = (
            f"mind-controller: prompt round {round_index}, revision {self._revision}"
            + (f"\n\nReason: {reasons}" if reasons else "")
        )
        commits = commit_prompt_files(self._pending_prompt_paths, message)
        self.prompt_commits.extend(commits)
        self._clear_pending_prompt_changes()
        return commits

    def _commit_restored_prompts(self, changed_paths: list[str]) -> list[PromptCommit]:
        self._pending_prompt_paths.update(Path(path) for path in changed_paths)
        if not self._pending_prompt_paths:
            return []
        message = (
            f"mind-controller: restore best prompt revision {self.best_revision}"
            f"\n\nBest evaluated round: {self.best_round}"
        )
        commits = commit_prompt_files(self._pending_prompt_paths, message)
        self.prompt_commits.extend(commits)
        self._clear_pending_prompt_changes()
        return commits

    def _clear_pending_prompt_changes(self) -> None:
        self._pending_prompt_paths.clear()
        self._pending_reasons.clear()

    def _stop_reason(self) -> str | None:
        if len(self.rounds) >= self.max_rounds:
            return "max_rounds"
        if (
            self.target_score is not None
            and self.best_score is not None
            and self.best_score >= self.target_score
        ):
            return "target_score"
        if len(self.rounds) - self.best_round >= self.stale_rounds:
            return "stale_rounds"
        return None

    def _restore_snapshot(self, snapshot: Mapping[Path, bytes]) -> list[str]:
        changed: list[str] = []
        for path, data in snapshot.items():
            if path.read_bytes() != data:
                _atomic_write(path, data)
                changed.append(str(path))
        return changed


def normalize_evaluation(raw: Any) -> Evaluation:
    """Accept the declared int score plus a structured multi-dimension extension."""

    if isinstance(raw, bool):
        raise TypeError("slave.eval() must return an int score, not bool")
    if isinstance(raw, int):
        return Evaluation(total=raw)
    if is_dataclass(raw):
        raw = asdict(raw)
    if not isinstance(raw, Mapping):
        raise TypeError("slave.eval() must return int or a score mapping")
    total_raw = raw.get("total", raw.get("score"))
    if isinstance(total_raw, bool) or not isinstance(total_raw, (int, float)):
        raise TypeError("structured evaluation requires numeric 'total' or 'score'")
    dimensions_raw = raw.get("dimensions", {})
    if not isinstance(dimensions_raw, Mapping):
        raise TypeError("evaluation 'dimensions' must be a mapping")
    dimensions: dict[str, float] = {}
    for key, value in dimensions_raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"evaluation dimension {key!r} must be numeric")
        dimensions[str(key)] = float(value)
    details = {
        str(key): _json_safe(value)
        for key, value in raw.items()
        if key not in {"total", "score", "dimensions"}
    }
    return Evaluation(total=int(total_raw), dimensions=dimensions, details=details)


def _evaluation_comparison(
    current: Evaluation,
    *,
    previous: Evaluation | None,
    best_score_before_round: int | None,
) -> dict[str, Any]:
    if previous is None:
        return {
            "outcome": "baseline",
            "previous_total": None,
            "total_delta": None,
            "best_total_before_round": best_score_before_round,
            "delta_from_best_before_round": None,
            "dimension_deltas": {},
        }

    total_delta = current.total - previous.total
    if total_delta > 0:
        outcome = "improved"
    elif total_delta < 0:
        outcome = "regressed"
    else:
        outcome = "unchanged"
    dimension_names = current.dimensions.keys() | previous.dimensions.keys()
    return {
        "outcome": outcome,
        "previous_total": previous.total,
        "total_delta": total_delta,
        "best_total_before_round": best_score_before_round,
        "delta_from_best_before_round": (
            current.total - best_score_before_round
            if best_score_before_round is not None
            else None
        ),
        "dimension_deltas": {
            name: current.dimensions.get(name, 0.0) - previous.dimensions.get(name, 0.0)
            for name in sorted(dimension_names)
        },
    }


def _resolve_prompt_paths(info: SlaveDebugInfo) -> tuple[Path, ...]:
    if not info.prompt_paths:
        raise ValueError("debug_info.prompt_paths must declare at least one prompt file")
    base = (info.working_directory or Path.cwd()).expanduser().resolve()
    resolved: list[Path] = []
    for raw in info.prompt_paths:
        path = raw.expanduser()
        path = (base / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_file():
            raise ValueError(f"declared prompt path is not a file: {path}")
        try:
            path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"declared prompt file is not UTF-8: {path}") from exc
        if path not in resolved:
            resolved.append(path)
    return tuple(resolved)


def _atomic_write(path: Path, data: bytes) -> None:
    mode = path.stat().st_mode
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _json_safe(value: Any) -> Any:
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return repr(value)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
