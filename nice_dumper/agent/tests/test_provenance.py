from pathlib import Path

from nice_dumper_agent.models import OptimizationRound, ScoreResult
from nice_dumper_agent.provenance import commit_initial_optimizer, commit_round


def test_provenance_commits_initial_and_round(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    optimizer = workspace / "optimize_xml.py"
    workspace.mkdir()
    optimizer.write_text("def optimize(xml_text: str) -> str:\n    return xml_text\n", encoding="utf-8")

    commit_initial_optimizer(workspace, optimizer)
    commit_round(
        workspace_dir=workspace,
        optimizer_path=optimizer,
        round_result=OptimizationRound(
            index=1,
            xml0_length=100,
            xml1_length=50,
            l0_count=1,
            l1_count=1,
            score=ScoreResult(90.0, 1.0, 0.5, 0, 0.0, []),
            reason="drop unused attributes",
            suggestion=optimizer.read_text(encoding="utf-8"),
        ),
    )

    assert (workspace / ".git").exists()
    assert (workspace / "rounds" / "round_0001.json").exists()
