from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from mind_controller_agent.contracts import SlaveDebugInfo
from mind_controller_agent.runtime import CognitiveController, normalize_evaluation


class FakeSlave:
    def __init__(self, prompt: Path) -> None:
        self.prompt = prompt
        self.results: list[dict[str, str]] = []
        self.evaluated_results: list[dict[str, str]] = []

    def run(self) -> dict[str, str]:
        result = {"prompt": self.prompt.read_text(encoding="utf-8")}
        self.results.append(result)
        return result

    def eval(self, result: dict[str, str]):
        self.evaluated_results.append(result)
        score = len(result["prompt"])
        return {"total": score, "dimensions": {"length": score}}


def make_controller(tmp_path: Path) -> tuple[CognitiveController, Path, FakeSlave]:
    prompt = tmp_path / "system.md"
    prompt.write_text("base", encoding="utf-8")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.name", "mind-controller-test")
    _git(tmp_path, "config", "user.email", "mind-controller-test@example.invalid")
    _git(tmp_path, "add", "system.md")
    _git(tmp_path, "commit", "-qm", "initial prompt")
    slave = FakeSlave(prompt)
    controller = CognitiveController(
        slave=slave,
        debug_info=SlaveDebugInfo(
            prompt_paths=(Path("system.md"),),
            prompt_structure="one system prompt",
            responsibility="test",
            expected_outcome="longer prompt scores higher",
            working_directory=tmp_path,
        ),
        max_rounds=3,
        target_score=None,
        stale_rounds=2,
    )
    return controller, prompt, slave


def test_run_eval_preserves_exact_result_and_tracks_best(tmp_path: Path) -> None:
    controller, prompt, slave = make_controller(tmp_path)

    run = json.loads(controller.run_slave_tool())
    score = json.loads(controller.eval_slave_tool(run["run_ref"]))
    controller.write_prompt_tool(str(prompt), "improved", "test hypothesis")
    run2 = json.loads(controller.run_slave_tool())
    score2 = json.loads(controller.eval_slave_tool(run2["run_ref"]))

    assert slave.results[0]["prompt"] == "base"
    assert slave.evaluated_results[0] is slave.results[0]
    assert score["total"] == 4
    assert score2["total"] == 8
    assert controller.best_round == 2
    assert len(run2["prompt_commits"]) == 1
    assert _git(tmp_path, "show", "HEAD:system.md").stdout == "improved"


def test_restore_best_discards_regressing_prompt(tmp_path: Path) -> None:
    controller, prompt, _ = make_controller(tmp_path)
    run = json.loads(controller.run_slave_tool())
    controller.eval_slave_tool(run["run_ref"])
    controller.write_prompt_tool(str(prompt), "much better", "increase useful detail")
    run2 = json.loads(controller.run_slave_tool())
    controller.eval_slave_tool(run2["run_ref"])
    controller.write_prompt_tool(str(prompt), "x", "bad experiment")
    run3 = json.loads(controller.run_slave_tool())
    controller.eval_slave_tool(run3["run_ref"])

    assert controller.restore_best_prompts()
    assert prompt.read_text(encoding="utf-8") == "much better"
    assert "restore best prompt revision" in _git(
        tmp_path,
        "log",
        "-1",
        "--format=%s",
    ).stdout


def test_cannot_edit_undeclared_file(tmp_path: Path) -> None:
    controller, _, _ = make_controller(tmp_path)
    other = tmp_path / "runtime.py"
    other.write_text("code", encoding="utf-8")
    run = json.loads(controller.run_slave_tool())
    controller.eval_slave_tool(run["run_ref"])

    with pytest.raises(ValueError, match="not a declared prompt"):
        controller.write_prompt_tool(str(other), "changed", "invalid")


def test_normalize_evaluation_supports_int_and_dimensions() -> None:
    assert normalize_evaluation(7).total == 7
    result = normalize_evaluation(
        {"score": 9, "dimensions": {"accuracy": 8.5}, "feedback": "ok"}
    )
    assert result.total == 9
    assert result.dimensions == {"accuracy": 8.5}
    assert result.details == {"feedback": "ok"}


def test_rejects_bool_score() -> None:
    with pytest.raises(TypeError, match="not bool"):
        normalize_evaluation(True)


def test_stop_condition_prevents_extra_slave_run(tmp_path: Path) -> None:
    controller, _, _ = make_controller(tmp_path)
    controller.target_score = 4
    run = json.loads(controller.run_slave_tool())
    controller.eval_slave_tool(run["run_ref"])

    stopped = json.loads(controller.should_stop_tool())
    extra_run = json.loads(controller.run_slave_tool())

    assert stopped == {
        "stop": True,
        "reason": "target_score",
        "rounds": 1,
        "best_round": 1,
        "best_score": 4,
    }
    assert extra_run == {"error": "controller has stopped: target_score"}


def test_prompt_commit_does_not_include_or_unstage_other_files(tmp_path: Path) -> None:
    controller, prompt, _ = make_controller(tmp_path)
    other = tmp_path / "other.txt"
    other.write_text("before", encoding="utf-8")
    _git(tmp_path, "add", "other.txt")
    _git(tmp_path, "commit", "-qm", "add other")

    run = json.loads(controller.run_slave_tool())
    controller.eval_slave_tool(run["run_ref"])
    other.write_text("staged user change", encoding="utf-8")
    _git(tmp_path, "add", "other.txt")
    controller.write_prompt_tool(str(prompt), "prompt revision", "focused prompt change")

    next_run = json.loads(controller.run_slave_tool())

    assert len(next_run["prompt_commits"]) == 1
    assert _git(tmp_path, "show", "--format=", "--name-only", "HEAD").stdout.strip() == "system.md"
    assert _git(tmp_path, "show", "HEAD:other.txt").stdout == "before"
    assert _git(tmp_path, "show", ":other.txt").stdout == "staged user change"
    assert _git(tmp_path, "status", "--short", "--", "other.txt").stdout == "M  other.txt\n"


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )
