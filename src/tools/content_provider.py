"""Workspace-scoped Excel output tools."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import json
import posixpath
import re
import string
import xml.etree.ElementTree as ET
import zipfile

from langchain_core.tools import StructuredTool

WORKSPACE_ROOT = Path(__file__).resolve().parents[2] / "workspace"
SHEET_NAME = "Sheet1"
MAIN_SHEET_PATH = "xl/worksheets/sheet1.xml"


class ContentProvider:
    """Create and update Excel files under the workspace directory."""

    def __init__(self, workspace_root: str | Path = WORKSPACE_ROOT) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def create_excel_file(
        self,
        relative_path: str,
        headers_json: str = "[]",
        rows_json: str = "[]",
    ) -> str:
        path = self._resolve_excel_path(relative_path)
        headers = _parse_json_rows(headers_json, allow_flat=True)
        rows = _parse_json_rows(rows_json, allow_flat=False)
        table: list[list[object]] = []
        if headers:
            table.append(headers[0])
        table.extend(rows)
        _write_xlsx(path, table)
        return str(path)

    def append_excel_rows(self, relative_path: str, rows_json: str) -> str:
        path = self._resolve_excel_path(relative_path)
        table = _read_xlsx(path) if path.exists() else []
        table.extend(_parse_json_rows(rows_json, allow_flat=False))
        _write_xlsx(path, table)
        return str(path)

    def update_excel_cell(self, relative_path: str, cell: str, value: str) -> str:
        path = self._resolve_excel_path(relative_path)
        table = _read_xlsx(path) if path.exists() else []
        row_index, col_index = _cell_to_indexes(cell)
        while len(table) <= row_index:
            table.append([])
        while len(table[row_index]) <= col_index:
            table[row_index].append("")
        table[row_index][col_index] = value
        _write_xlsx(path, table)
        return str(path)

    def _resolve_excel_path(self, relative_path: str) -> Path:
        normalized = posixpath.normpath(str(relative_path or "").replace("\\", "/")).lstrip("/")
        if normalized in {"", "."}:
            raise ValueError("relative_path is required")
        path = (self.workspace_root / normalized).resolve()
        if self.workspace_root != path and self.workspace_root not in path.parents:
            raise ValueError("Excel path must stay inside workspace")
        if path.suffix.lower() != ".xlsx":
            raise ValueError("Excel path must end with .xlsx")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def create_content_provider_tools(provider: ContentProvider | None = None) -> list[StructuredTool]:
    content_provider = provider or ContentProvider()
    return [
        StructuredTool.from_function(
            func=content_provider.create_excel_file,
            name="create_excel_file",
            description=(
                "Create an .xlsx file under workspace. Inputs: relative_path, "
                "headers_json as a JSON array, rows_json as a JSON array of rows."
            ),
        ),
        StructuredTool.from_function(
            func=content_provider.append_excel_rows,
            name="append_excel_rows",
            description=(
                "Append rows to an .xlsx file under workspace. rows_json must be "
                "a JSON array of row arrays or objects."
            ),
        ),
        StructuredTool.from_function(
            func=content_provider.update_excel_cell,
            name="update_excel_cell",
            description="Update one cell in an .xlsx file under workspace, e.g. cell='B2'.",
        ),
    ]


def build_tools() -> list[StructuredTool]:
    return create_content_provider_tools()


def _parse_json_rows(value: str, allow_flat: bool) -> list[list[object]]:
    if not value:
        return []
    parsed = json.loads(value)
    if allow_flat and isinstance(parsed, list) and not _is_row_like(parsed):
        return [parsed]
    if isinstance(parsed, dict):
        return [[parsed[key] for key in parsed]]
    if not isinstance(parsed, list):
        raise ValueError("Expected JSON array")
    if not parsed:
        return []
    if allow_flat and not any(_is_row_like(item) for item in parsed):
        return [parsed]
    rows: list[list[object]] = []
    for item in parsed:
        if isinstance(item, dict):
            rows.append([item[key] for key in item])
        elif isinstance(item, list):
            rows.append(item)
        else:
            rows.append([item])
    return rows


def _is_row_like(value: object) -> bool:
    return isinstance(value, (list, dict))


def _write_xlsx(path: Path, rows: list[list[object]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types_xml())
        archive.writestr("_rels/.rels", _root_rels_xml())
        archive.writestr("xl/workbook.xml", _workbook_xml())
        archive.writestr("xl/_rels/workbook.xml.rels", _workbook_rels_xml())
        archive.writestr(MAIN_SHEET_PATH, _worksheet_xml(rows))


def _read_xlsx(path: Path) -> list[list[object]]:
    with zipfile.ZipFile(path, "r") as archive:
        sheet_xml = archive.read(MAIN_SHEET_PATH)
    root = ET.fromstring(sheet_xml)
    rows: list[list[object]] = []
    for row in root.findall(".//{*}sheetData/{*}row"):
        values: list[object] = []
        for cell in row.findall("{*}c"):
            col_index = _column_name_to_index(re.sub(r"\d+", "", cell.attrib.get("r", "")))
            while len(values) < col_index - 1:
                values.append("")
            values.append(_cell_value(cell))
        rows.append(values)
    return rows


def _cell_value(cell: ET.Element) -> str:
    inline_text = cell.find("{*}is/{*}t")
    if inline_text is not None:
        return inline_text.text or ""
    value = cell.find("{*}v")
    return "" if value is None else (value.text or "")


def _worksheet_xml(rows: list[list[object]]) -> str:
    row_xml = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            ref = f"{_column_index_to_name(col_index)}{row_index}"
            cells.append(_cell_xml(ref, value))
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(row_xml)}</sheetData>'
        "</worksheet>"
    )


def _cell_xml(ref: str, value: object) -> str:
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{1 if value else 0}</v></c>'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return f'<c r="{ref}"><v>{value}</v></c>'
    return f'<c r="{ref}" t="inlineStr"><is><t>{_escape_xml(value)}</t></is></c>'


def _escape_xml(value: object) -> str:
    return (
        str(value if value is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _cell_to_indexes(cell: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Za-z]+)([1-9][0-9]*)", str(cell or "").strip())
    if not match:
        raise ValueError("cell must look like A1, B2, etc.")
    return int(match.group(2)) - 1, _column_name_to_index(match.group(1)) - 1


def _column_name_to_index(name: str) -> int:
    index = 0
    for char in name.upper():
        if char not in string.ascii_uppercase:
            raise ValueError(f"Invalid column name: {name}")
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def _column_index_to_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(ord("A") + remainder) + name
    return name


def _content_types_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )


def _root_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _workbook_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{SHEET_NAME}" sheetId="1" r:id="rId1"/></sheets>'
        "</workbook>"
    )


def _workbook_rels_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
