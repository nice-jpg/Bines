from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.messages.tool import tool_call
from openpyxl import Workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def _raw_run():
    return importlib.import_module("raw_run")


def _write_workbook(path: Path, sheets: list[tuple[str, list[list[object]]]]) -> None:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, rows in sheets:
        worksheet = workbook.create_sheet(title)
        for row in rows:
            worksheet.append(row)
    workbook.save(path)
    workbook.close()


def _ai_call(*calls) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))


def test_eval_combines_quantity_context_and_correctness_grades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_run = _raw_run()
    result_path = tmp_path / "result.xlsx"
    truth_path = tmp_path / "truth.xlsx"
    _write_workbook(
        result_path,
        [
            (
                "result-a-1",
                [
                    ["merchant_name", "product_name"],
                    [" A店 ", " 商品1 "],
                    ["A店", "商品1"],
                    ["A店", "商品2"],
                    ["A店", ""],
                ],
            ),
            (
                "result-a-2",
                [
                    ["merchant_name", "product_name"],
                    ["A店", "商品3"],
                ],
            ),
            ("B店", [["product_name"], ["商品4"]]),
            ("result-only", [["product_name"], ["不参与正确性比较"]]),
            ("empty", []),
        ],
    )
    _write_workbook(
        truth_path,
        [
            (
                "truth-a",
                [
                    ["merchant_name", "商品名"],
                    ["A店", "商品1"],
                    ["A店", "真值商品"],
                ],
            ),
            ("B店", [["商品名"], ["商品4"]]),
            ("truth-only", [["商品名"], ["不参与比较"]]),
        ],
    )
    messages = [
        HumanMessage(content="执行应用探测任务"),
        _ai_call(
            tool_call(
                name="notify_user",
                args={"current_path": "meituan/美食"},
                id="notice-list",
            ),
            tool_call(name="run_package", args={}, id="run-package"),
        ),
        ToolMessage(content="ok", tool_call_id="run-package"),
        _ai_call(
            tool_call(
                name="notify_user",
                args={"current_path": "meituan/美食/商家"},
                id="notice-merchant",
            ),
            tool_call(name="uiautomate", args={}, id="ui"),
            tool_call(name="query_manual", args={}, id="manual"),
        ),
        ToolMessage(content="xml", tool_call_id="ui"),
        _ai_call(tool_call(name="think", args={"thought": "inspect"}, id="think")),
        _ai_call(
            tool_call(
                name="notify_user",
                args={"current_path": "meituan/美食"},
                id="notice-back",
            ),
            tool_call(name="swipe_up", args={}, id="swipe"),
        ),
    ]
    result = SimpleNamespace(state={"messages": messages})
    monkeypatch.setattr(raw_run, "RESULT_WORKBOOK_PATH", result_path)
    monkeypatch.setattr(raw_run, "TRUTH_WORKBOOK_PATH", truth_path)

    # The result-only merchant is counted but is not part of correctness comparison.
    assert raw_run.eval(result) == {
        "total": 28,
        "dimensions": {
            "result_count_grade": 8,
            "context_grade": 40,
            "correctness_grade": -20,
        },
        "merchant_count": 3,
        "product_count": 5,
        "incorrect_product_count": 2,
    }


def test_context_grade_returns_zero_without_operations() -> None:
    raw_run = _raw_run()
    result = SimpleNamespace(
        state={
            "messages": [
                HumanMessage(content="start"),
                _ai_call(
                    tool_call(
                        name="notify_user",
                        args={"current_path": "meituan/美食/商家"},
                        id="notice",
                    )
                ),
            ]
        }
    )

    assert raw_run._context_grade(result) == 0


def test_workbook_rejects_multiple_merchants_in_one_worksheet(tmp_path: Path) -> None:
    raw_run = _raw_run()
    path = tmp_path / "invalid.xlsx"
    _write_workbook(
        path,
        [
            (
                "mixed",
                [
                    ["merchant_name", "product_name"],
                    ["A店", "商品1"],
                    ["B店", "商品2"],
                ],
            )
        ],
    )

    with pytest.raises(ValueError, match="multiple merchant_name values"):
        raw_run._read_merchant_products(path)


def test_workbook_requires_product_name_header(tmp_path: Path) -> None:
    raw_run = _raw_run()
    path = tmp_path / "invalid.xlsx"
    _write_workbook(path, [("A店", [["merchant_name", "name"], ["A店", "商品1"]])])

    with pytest.raises(ValueError, match="product_name"):
        raw_run._read_merchant_products(path)


def test_workbook_wraps_malformed_xlsx_error(tmp_path: Path) -> None:
    raw_run = _raw_run()
    path = tmp_path / "broken.xlsx"
    path.write_bytes(b"not an xlsx archive")

    with pytest.raises(ValueError, match="Unable to read evaluation workbook"):
        raw_run._read_merchant_products(path)


def test_eval_raises_when_truth_workbook_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_run = _raw_run()
    result_path = tmp_path / "result.xlsx"
    _write_workbook(result_path, [("A店", [["product_name"], ["商品1"]])])
    monkeypatch.setattr(raw_run, "RESULT_WORKBOOK_PATH", result_path)
    monkeypatch.setattr(raw_run, "TRUTH_WORKBOOK_PATH", tmp_path / "missing.xlsx")

    with pytest.raises(FileNotFoundError, match="missing.xlsx"):
        raw_run.eval(SimpleNamespace(state={"messages": []}))


@pytest.mark.parametrize(
    "state",
    [
        None,
        {},
        {"messages": "not-a-message-list"},
        {"messages": [{"role": "user", "content": "not BaseMessage"}]},
    ],
)
def test_context_grade_rejects_invalid_agent_state(state) -> None:
    raw_run = _raw_run()

    with pytest.raises(ValueError):
        raw_run._context_grade(SimpleNamespace(state=state))
