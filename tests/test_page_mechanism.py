from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import page_mechanism as page_mechanism_module
from codex_apply_patch import __version__ as codex_apply_patch_version
from page_mechanism import CollectorTrainingRegistry, PageMechanism, eval_collection


def test_query_page_and_task_docs_are_separate(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan" / "外卖"
    page_dir.mkdir(parents=True)
    (page_dir / "PAGE.md").write_text("page structure", encoding="utf-8")
    (page_dir / "task.md").write_text("task flow", encoding="utf-8")

    mechanism = PageMechanism(tmp_path)

    page = mechanism.query_page_doc("com.sankuai.meituan/外卖")
    task = mechanism.query_task_doc("meituan/外卖")

    assert "page structure" in page
    assert "task flow" not in page
    assert "task flow" in task
    assert "page structure" not in task


def test_run_py_is_page_scoped_and_rejects_path_escape(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    mechanism = PageMechanism(tmp_path)

    result = mechanism.write_run_py("meituan", "def run(xml: str):\n    return {'items': []}\n")
    rejected = mechanism.write_run_py("../secret", "bad")

    assert json.loads(result)["ok"] is True
    assert (page_dir / "run.py").is_file()
    assert "must not contain '..'" in rejected


def test_codex_apply_patch_dependency_contract() -> None:
    assert codex_apply_patch_version == "0.4.0"


def test_apply_run_py_patch_updates_multiple_chunks_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.py"
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
*** Update File: run.py
@@
-PREFIX = 'old'
+PREFIX = 'new'
@@
-    return {'items': [], 'source': PREFIX}
+    return {'items': [xml], 'source': PREFIX}
*** End Patch"""

    result = json.loads(PageMechanism(tmp_path).apply_run_py_patch("meituan", patch))

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
        "*** Begin Patch\n*** Add File: run.py\n+bad\n*** End Patch",
        "*** Begin Patch\n*** Delete File: run.py\n*** End Patch",
        (
            "*** Begin Patch\n*** Update File: run.py\n*** Move to: moved.py\n"
            "@@\n-def run(xml):\n+def run(xml: str):\n*** End Patch"
        ),
        (
            "*** Begin Patch\n*** Update File: other.py\n"
            "@@\n-old\n+new\n*** End Patch"
        ),
        (
            "*** Begin Patch\n*** Update File: ../run.py\n"
            "@@\n-old\n+new\n*** End Patch"
        ),
        (
            "*** Begin Patch\n*** Update File: run.py\n@@\n-def run(xml):\n+def run(xml: str):\n"
            "*** Update File: other.py\n@@\n-old\n+new\n*** End Patch"
        ),
    ],
)
def test_apply_run_py_patch_rejects_operations_outside_single_run_py(
    tmp_path: Path,
    patch: str,
) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.py"
    original = "def run(xml):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")

    result = json.loads(PageMechanism(tmp_path).apply_run_py_patch("meituan", patch))

    assert result["ok"] is False
    assert result["applied"] is False
    assert result["error_type"] == "PatchScopeError"
    assert run_path.read_text(encoding="utf-8") == original


def test_apply_run_py_patch_rejects_invalid_format_conflict_and_invalid_python(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.py"
    original = "def run(xml):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")
    mechanism = PageMechanism(tmp_path)

    malformed = json.loads(mechanism.apply_run_py_patch("meituan", "not a patch"))
    conflict = json.loads(
        mechanism.apply_run_py_patch(
            "meituan",
            "*** Begin Patch\n*** Update File: run.py\n@@\n-missing\n+new\n*** End Patch",
        )
    )
    invalid_python = json.loads(
        mechanism.apply_run_py_patch(
            "meituan",
            (
                "*** Begin Patch\n*** Update File: run.py\n@@\n"
                "-    return {'items': []}\n+    return {'items': [\n*** End Patch"
            ),
        )
    )

    assert malformed["error_type"] == "InvalidPatchFormat"
    assert conflict["error_type"] == "PatchConflict"
    assert conflict["error"] == "Patch context does not match the current run.py."
    assert invalid_python["error_type"] == "InvalidPython"
    assert invalid_python["line"] == 2
    assert run_path.read_text(encoding="utf-8") == original


def test_apply_run_py_patch_rejects_invalid_engine_result_without_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.py"
    original = "def run(xml):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")

    class InvalidResult:
        added = ["other.py"]
        deleted = []
        modified = ["run.py"]
        files = {"run.py": original, "other.py": "bad"}

    monkeypatch.setattr(
        page_mechanism_module,
        "apply_patch_in_memory",
        lambda patch, files: InvalidResult(),
    )
    patch = (
        "*** Begin Patch\n*** Update File: run.py\n@@\n"
        "-def run(xml):\n+def run(xml: str):\n*** End Patch"
    )

    result = json.loads(PageMechanism(tmp_path).apply_run_py_patch("meituan", patch))

    assert result["error_type"] == "PatchResultError"
    assert run_path.read_text(encoding="utf-8") == original


def test_apply_run_py_patch_commit_failure_preserves_file_and_removes_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    run_path = page_dir / "run.py"
    original = "def run(xml):\n    return {'items': []}\n"
    run_path.write_text(original, encoding="utf-8")
    monkeypatch.setattr(
        page_mechanism_module.os,
        "replace",
        lambda source, destination: (_ for _ in ()).throw(OSError("commit failed")),
    )
    patch = (
        "*** Begin Patch\n*** Update File: run.py\n@@\n"
        "-def run(xml):\n+def run(xml: str):\n*** End Patch"
    )

    result = json.loads(PageMechanism(tmp_path).apply_run_py_patch("meituan", patch))

    assert result["error_type"] == "PatchCommitError"
    assert run_path.read_text(encoding="utf-8") == original
    assert list(page_dir.glob(".run.py.*.tmp")) == []


def test_sniff_run_uses_only_current_xml_and_persists_latest_json_state(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.py").write_text(
        "def run(xml: str):\n"
        "    return {'items': [{'name': xml}]}\n",
        encoding="utf-8",
    )
    mechanism = PageMechanism(tmp_path)

    first = json.loads(mechanism.sniff_run("meituan", "one"))
    second = json.loads(mechanism.sniff_run("meituan", "two"))

    assert first["ok"] is True
    assert first["result"] == {"items": [{"name": "one"}]}
    assert second["result"] == {"items": [{"name": "two"}]}
    assert mechanism.state_for("meituan") == {"items": [{"name": "two"}]}


def test_run_py_exception_returns_structured_repair_signal(tmp_path: Path) -> None:
    page_dir = tmp_path / "meituan"
    page_dir.mkdir()
    (page_dir / "run.py").write_text("def run(xml: str):\n    raise ValueError('bad parse')\n", encoding="utf-8")

    result = json.loads(PageMechanism(tmp_path).sniff_run("meituan", "<xml/>"))

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
    mechanism = PageMechanism(tmp_path)

    with pytest.raises(FileNotFoundError, match="task.md"):
        mechanism.validate_page_manual("meituan")

    (page_dir / "task.md").write_text("task", encoding="utf-8")
    assert mechanism.validate_page_manual("com.sankuai.meituan") == "meituan"


def test_strict_document_queries_raise_for_invalid_page(tmp_path: Path) -> None:
    mechanism = PageMechanism(tmp_path)

    with pytest.raises(FileNotFoundError, match="PAGE.md"):
        mechanism.query_page_doc("meituan/missing")


def test_merchant_policy_is_structured_and_not_inferred_from_path_suffix(tmp_path: Path) -> None:
    mechanism = PageMechanism(tmp_path)

    policy = mechanism.page_policy("meituan/外卖/美食/商家")
    unknown = mechanism.page_policy("another/商家")

    assert policy.role == "merchant"
    assert policy.requires_collector is True
    assert policy.max_product_entry_taps == 1
    assert unknown.role == "page"
    assert unknown.requires_collector is False


def test_training_registry_isolates_same_path_by_visit_id() -> None:
    registry = CollectorTrainingRegistry()
    registry.mark_trained(
        "visit-a",
        "meituan/美食/商家",
        merchant_profile={"name": "商家A"},
        script_fingerprint="script-a",
        restoration={"restored": True},
    )

    assert registry.is_trained("visit-a", "meituan/美食/商家") is True
    assert registry.is_trained("visit-b", "meituan/美食/商家") is False
    record = registry.record_for("visit-a")
    assert record is not None
    assert record.merchant_profile == {"name": "商家A"}
    assert record.restoration == {"restored": True}
