from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from page_mechanism import PageMechanism, eval_collection


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
