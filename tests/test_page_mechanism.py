from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import src.page.mechanism as page_mechanism_module
from codex_apply_patch import __version__ as codex_apply_patch_version
from src.collector.training import CollectorTrainingRegistry
from page_helpers import page_mechanism
from src.page.mechanism import PageMechanism
from src.collector.evaluation import eval_collection


def test_query_page_and_task_docs_are_separate(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan" / "外卖"
    page_dir.mkdir(parents=True)
    (page_dir / "PAGE.md").write_text("page structure", encoding="utf-8")
    (page_dir / "task.md").write_text("task flow", encoding="utf-8")

    mechanism = page_mechanism(tmp_path)

    page = mechanism.query_page_doc("com.sankuai.meituan/外卖")
    task = mechanism.query_task_doc("meituan/外卖")

    assert "page structure" in page
    assert "task flow" not in page
    assert "task flow" in task
    assert "page structure" not in task


def test_run_agent_py_is_page_scoped_and_rejects_path_escape(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    mechanism = page_mechanism(tmp_path)

    result = mechanism.write_run_agent_py("meituan", "def run(xml: str):\n    return {'items': []}\n")
    rejected = mechanism.write_run_agent_py("../secret", "bad")

    assert json.loads(result)["ok"] is True
    assert (page_dir / "run.agent.py").is_file()
    assert "must not contain '..'" in rejected


def test_codex_apply_patch_dependency_contract() -> None:
    assert codex_apply_patch_version == "0.4.0"


def test_apply_run_agent_py_patch_updates_multiple_chunks_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.agent.py"
    original = (
        "PREFIX = 'old'\n"
        "\n"
        "def run(xml: str):\n"
        "    return {'items': [], 'source': PREFIX}\n"
    )
    updated = (
        "PREFIX = 'new'\n"
        "\n"
        "def run(xml: str):\n"
        "    return {'items': [xml], 'source': PREFIX}\n"
    )
    run_path.write_text(original, encoding="utf-8")
    real_replace = page_mechanism_module.os.replace
    versions_seen_at_commit: list[tuple[str, str]] = []

    def observed_replace(source, destination):
        versions_seen_at_commit.append(
            (
                Path(destination).read_text(encoding="utf-8"),
                Path(source).read_text(encoding="utf-8"),
            )
        )
        return real_replace(source, destination)

    monkeypatch.setattr(page_mechanism_module.os, "replace", observed_replace)
    patch = """*** Begin Patch
*** Update File: run.agent.py
@@
-PREFIX = 'old'
+PREFIX = 'new'
@@
-    return {'items': [], 'source': PREFIX}
+    return {'items': [xml], 'source': PREFIX}
*** End Patch"""

    result = json.loads(page_mechanism(tmp_path).apply_run_agent_py_patch("meituan", patch))

    assert result["ok"] is True
    assert result["applied"] is True
    assert result["bytes_before"] == len(original.encode("utf-8"))
    assert result["bytes_after"] == len(updated.encode("utf-8"))
    assert len(result["script_fingerprint"]) == 64
    assert versions_seen_at_commit == [(original, updated)]
    assert run_path.read_text(encoding="utf-8") == updated


@pytest.mark.parametrize(
    "patch",
    [
        "*** Begin Patch\n*** Add File: run.agent.py\n+bad\n*** End Patch",
        "*** Begin Patch\n*** Delete File: run.agent.py\n*** End Patch",
        (
            "*** Begin Patch\n*** Update File: run.agent.py\n*** Move to: moved.py\n"
            "@@\n-def run(xml):\n+def run(xml: str):\n*** End Patch"
        ),
        (
            "*** Begin Patch\n*** Update File: other.py\n"
            "@@\n-old\n+new\n*** End Patch"
        ),
        (
            "*** Begin Patch\n*** Update File: ../run.agent.py\n"
            "@@\n-old\n+new\n*** End Patch"
        ),
        (
            "*** Begin Patch\n*** Update File: run.agent.py\n@@\n-def run(xml):\n+def run(xml: str):\n"
            "*** Update File: other.py\n@@\n-old\n+new\n*** End Patch"
        ),
    ],
)
def test_apply_run_agent_py_patch_rejects_operations_outside_single_run_agent_py(
    tmp_path: Path,
    patch: str,
) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.agent.py"
    original = "def run(xml):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")

    result = json.loads(page_mechanism(tmp_path).apply_run_agent_py_patch("meituan", patch))

    assert result["ok"] is False
    assert result["applied"] is False
    assert result["error_type"] == "PatchScopeError"
    assert run_path.read_text(encoding="utf-8") == original


def test_apply_run_agent_py_patch_rejects_invalid_format_conflict_and_invalid_python(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.agent.py"
    original = "def run(xml):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")
    mechanism = page_mechanism(tmp_path)

    malformed = json.loads(mechanism.apply_run_agent_py_patch("meituan", "not a patch"))
    conflict = json.loads(
        mechanism.apply_run_agent_py_patch(
            "meituan",
            "*** Begin Patch\n*** Update File: run.agent.py\n@@\n-missing\n+new\n*** End Patch",
        )
    )
    invalid_python = json.loads(
        mechanism.apply_run_agent_py_patch(
            "meituan",
            (
                "*** Begin Patch\n*** Update File: run.agent.py\n@@\n"
                "-    return {'items': []}\n+    return {'items': [\n*** End Patch"
            ),
        )
    )

    assert malformed["error_type"] == "InvalidPatchFormat"
    assert conflict["error_type"] == "PatchConflict"
    assert conflict["error"] == "Patch context does not match the current run.agent.py."
    assert invalid_python["error_type"] == "InvalidPython"
    assert invalid_python["line"] == 2
    assert run_path.read_text(encoding="utf-8") == original


def test_apply_run_agent_py_patch_rejects_invalid_engine_result_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.agent.py"
    original = "def run(xml):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")

    class InvalidResult:
        added = ["other.py"]
        deleted = []
        modified = ["run.agent.py"]
        files = {"run.agent.py": original, "other.py": "bad"}

    monkeypatch.setattr(
        page_mechanism_module,
        "apply_patch_in_memory",
        lambda patch, files: InvalidResult(),
    )
    patch = (
        "*** Begin Patch\n*** Update File: run.agent.py\n@@\n"
        "-def run(xml):\n+def run(xml: str):\n*** End Patch"
    )

    result = json.loads(page_mechanism(tmp_path).apply_run_agent_py_patch("meituan", patch))

    assert result["error_type"] == "PatchResultError"
    assert run_path.read_text(encoding="utf-8") == original


def test_apply_run_agent_py_patch_commit_failure_preserves_file_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.agent.py"
    original = "def run(xml):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        page_mechanism_module.os,
        "replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("commit failed")),
    )
    patch = (
        "*** Begin Patch\n*** Update File: run.agent.py\n@@\n"
        "-def run(xml):\n+def run(xml: str):\n*** End Patch"
    )

    result = json.loads(page_mechanism(tmp_path).apply_run_agent_py_patch("meituan", patch))

    assert result["error_type"] == "PatchCommitError"
    assert run_path.read_text(encoding="utf-8") == original
    assert list(page_dir.glob(".run.agent.py.*.tmp")) == []


def _view(text):
    from src.device.services.page.bridge import PageBridge
    from src.device.services.page.view import ReadOnlyPageView
    bridge = PageBridge(lambda: f'<hierarchy><node text="{text}" class="android.widget.Button" bounds="[0,200][1000,1800]"/></hierarchy>')
    content, _ = bridge.refresh()
    bridge.query("update_aaid", {"id": content["regions"][0]["id"], "value": "1"})
    return ReadOnlyPageView(bridge.snapshot())


def test_sniff_run_uses_current_view_and_persists_latest_json_state(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text(
        "def run(view):\n"
        "    return {'items': [{'name': view.page_read(view.select_aaid(1)['id'])['entries'][0]['value']}]}\n",
        encoding="utf-8",
    )
    mechanism = page_mechanism(tmp_path)

    (page_dir / "COLLECT.md").write_text('{"required_aliases":["1"]}\nProduct region.')
    first = json.loads(mechanism.sniff_run("meituan", _view("one")))
    second = json.loads(mechanism.sniff_run("meituan", _view("two")))

    assert first["ok"] is True
    assert first["result"] == {"items": [{"name": "one"}]}
    assert second["result"] == {"items": [{"name": "two"}]}
    assert mechanism.state_for("meituan") == {"items": [{"name": "two"}]}


def test_run_agent_py_exception_returns_structured_repair_signal(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.agent.py").write_text("def run(view):\n    raise ValueError('bad parse')\n", encoding="utf-8")
    (page_dir / "COLLECT.md").write_text('{"required_aliases":["1"]}\nProduct region.')

    result = json.loads(page_mechanism(tmp_path).sniff_run("meituan", _view("cup")))

    assert result["ok"] is False
    assert result["error_type"] == "ValueError"
    assert result["error"] == "bad parse"
    assert "traceback" in result


def test_eval_collection_scores_exact_match_and_mismatch() -> None:
    assert eval_collection({"items": [{"name": "cup"}]}, {"items": [{"name": "cup"}]}) == {
        "score": 100,
        "errors": [],
        "missing": [],
    }
    mismatch = eval_collection(
        {"merchant": {"distance": "1km"}, "items": [{"name": "cup"}, {"name": "bowl"}, {"name": "plate"}]},
        {"merchant": {"distance": "2km"}, "items": [{"name": "cup"}]},
    )
    assert mismatch["score"] < 100
    assert mismatch["errors"] == ["result.merchant.distance"]
    assert mismatch["missing"] == ["result.items[1]", "result.items[2]"]


def test_validate_page_manual_requires_both_documents(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "PAGE.md").write_text("page", encoding="utf-8")
    mechanism = page_mechanism(tmp_path)

    with pytest.raises(FileNotFoundError, match="task.md"):
        mechanism.validate_page_manual("meituan")

    (page_dir / "task.md").write_text("task", encoding="utf-8")
    assert mechanism.validate_page_manual("com.sankuai.meituan") == "meituan"


def test_strict_document_queries_raise_for_invalid_page(tmp_path: Path) -> None:
    mechanism = page_mechanism(tmp_path)

    with pytest.raises(FileNotFoundError, match="PAGE.md"):
        mechanism.query_page_doc("meituan/missing")


def test_merchant_policy_is_structured_and_not_inferred_from_path_suffix(tmp_path: Path) -> None:
    from page_strategy_helpers import mechanism as create_pages
    mechanism = create_pages(tmp_path)
    from src.strategy.general.declarations import read_page_strategy, requires_collection
    assert read_page_strategy(mechanism, "meituan/外卖/美食/商家") == "merchant"
    assert requires_collection(mechanism, "meituan/外卖/美食/商家")
    with pytest.raises(FileNotFoundError):
        read_page_strategy(mechanism, "another/商家")


def test_training_registry_isolates_same_path_by_visit_id() -> None:
    registry = CollectorTrainingRegistry()
    registry.mark_trained(
        "visit-a",
        "meituan/美食/商家",
        merchant_profile={"name": "商家A"},
        script_fingerprint="script-a",
        restoration={"restored": True, "strategy": "reset_tap", "tap_attempted": True},
    )

    assert registry.is_trained("visit-a", "meituan/美食/商家") is True
    assert registry.is_trained("visit-b", "meituan/美食/商家") is False
    record = registry.record_for("visit-a")
    assert record is not None
    assert record.merchant_profile == {"name": "商家A"}
    assert record.restoration == {"restored": True, "strategy": "reset_tap", "tap_attempted": True}
