"""Git-backed provenance for optimizer script evolution."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict
from pathlib import Path

from .models import OptimizationRound


class ProvenanceError(RuntimeError):
    """Raised when workspace provenance cannot be recorded."""


def ensure_workspace_repo(workspace_dir: Path) -> None:
    workspace_dir.mkdir(parents=True, exist_ok=True)
    if not (workspace_dir / ".git").exists():
        _git(workspace_dir, "init")
    _git(workspace_dir, "config", "user.name", "nice-dumper-agent")
    _git(workspace_dir, "config", "user.email", "nice-dumper-agent@example.invalid")


def commit_initial_optimizer(workspace_dir: Path, optimizer_path: Path) -> None:
    ensure_workspace_repo(workspace_dir)
    _git(workspace_dir, "add", _relative(workspace_dir, optimizer_path))
    if _has_staged_changes(workspace_dir):
        _git(workspace_dir, "commit", "-m", "Initialize optimizer script")


def commit_round(
    *,
    workspace_dir: Path,
    optimizer_path: Path,
    round_result: OptimizationRound,
) -> None:
    ensure_workspace_repo(workspace_dir)
    rounds_dir = workspace_dir / "rounds"
    rounds_dir.mkdir(parents=True, exist_ok=True)
    report_path = rounds_dir / f"round_{round_result.index:04d}.json"
    report_path.write_text(json.dumps(asdict(round_result), ensure_ascii=False, indent=2), encoding="utf-8")
    _git(workspace_dir, "add", _relative(workspace_dir, optimizer_path), _relative(workspace_dir, report_path))
    if _has_staged_changes(workspace_dir):
        _git(workspace_dir, "commit", "-m", _commit_message(round_result))


def _commit_message(round_result: OptimizationRound) -> str:
    score = round_result.score
    reason = " ".join(round_result.reason.strip().split())
    if len(reason) > 160:
        reason = reason[:157] + "..."
    return (
        f"Optimize XML round {round_result.index}: score={score.score:.2f}, "
        f"compression={score.compression:.3f}, missing={score.missing_count}\n\n"
        f"Reason: {reason or 'no model-provided reason'}"
    )


def _has_staged_changes(workspace_dir: Path) -> bool:
    result = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=workspace_dir,
        check=False,
        text=True,
        capture_output=True,
        env=_git_env(workspace_dir),
    )
    return result.returncode == 1


def _git(workspace_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace_dir,
        check=False,
        text=True,
        capture_output=True,
        env=_git_env(workspace_dir),
    )
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        raise ProvenanceError(output or f"git {' '.join(args)} failed")
    return result.stdout


def _relative(root: Path, path: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _git_env(workspace_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_CEILING_DIRECTORIES"] = str(workspace_dir.resolve().parent)
    return env
