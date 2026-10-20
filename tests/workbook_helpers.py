"""Read workbook values for storage assertions."""
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.worksheet.worksheet import Worksheet
from src.common.utils.json import JsonValue


def read_xlsx(path: Path, sheet_name: str = "Sheet1") -> list[list[JsonValue]]:
    workbook = load_workbook(path, data_only=True)
    try:
        worksheet = workbook[sheet_name]
        if not isinstance(worksheet, Worksheet):
            raise TypeError("Expected a worksheet")
        rows: list[list[JsonValue]] = []
        for row in worksheet.iter_rows(values_only=True):
            values: list[JsonValue] = [_read_cell_value(value) for value in row]
            while values and values[-1] == "":
                values.pop()
            rows.append(values)
        while rows and not rows[-1]:
            rows.pop()
        return rows
    finally:
        workbook.close()


def _read_cell_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)


def workbook_service(workspace_root: str | Path):
    from unittest.mock import patch
    from src.tools.workbooks.excel import ExcelWorkbookService

    with patch("src.tools.workbooks.excel.WORKSPACE_ROOT", Path(workspace_root)):
        return ExcelWorkbookService()


def collection_sink(provider):
    from unittest.mock import patch
    from src.tools.workbooks.excel import CollectionWorkbookSink

    with patch("src.tools.workbooks.excel.ExcelWorkbookService", return_value=provider):
        return CollectionWorkbookSink()
