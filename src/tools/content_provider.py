"""Workspace-scoped Excel output tools."""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import posixpath
import re
import tempfile
import threading
from typing import Any, Iterator, Mapping
from zipfile import BadZipFile, ZipFile

from langchain_core.tools import StructuredTool
from openpyxl import Workbook, load_workbook
from openpyxl.workbook.workbook import Workbook as OpenpyxlWorkbook
from openpyxl.worksheet.worksheet import Worksheet

WORKSPACE_ROOT = Path(__file__).resolve().parents[2] / "workspace"
SHEET_NAME = "Sheet1"
INVALID_SHEET_NAME_RE = re.compile(r"[\[\]:*?/\\]")
_PATH_LOCKS: dict[Path, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


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
        sheet_name: str = SHEET_NAME,
    ) -> str:
        path = self._resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            validated_name = _validate_sheet_name(sheet_name)
            workbook = Workbook()
            worksheet = workbook.active
            worksheet.title = validated_name
            _replace_sheet_rows(worksheet, _table_from_json(headers_json, rows_json))
            _save_workbook(workbook, path)
        return str(path)

    def create_excel_sheet(
        self,
        relative_path: str,
        sheet_name: str,
        headers_json: str = "[]",
        rows_json: str = "[]",
    ) -> str:
        path = self._resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            workbook = _load_existing_workbook(path)
            try:
                validated_name = _validate_sheet_name(sheet_name)
                _ensure_unique_sheet_name(workbook, validated_name)
                worksheet = workbook.create_sheet(validated_name)
                _replace_sheet_rows(worksheet, _table_from_json(headers_json, rows_json))
                _save_workbook(workbook, path)
            except Exception:
                workbook.close()
                raise
        return str(path)

    def rename_excel_sheet(self, relative_path: str, sheet_name: str, new_sheet_name: str) -> str:
        path = self._resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            workbook = _load_existing_workbook(path)
            try:
                worksheet = _get_sheet(workbook, sheet_name)
                validated_name = _validate_sheet_name(new_sheet_name)
                if validated_name.casefold() != worksheet.title.casefold():
                    _ensure_unique_sheet_name(workbook, validated_name)
                worksheet.title = validated_name
                _save_workbook(workbook, path)
            except Exception:
                workbook.close()
                raise
        return str(path)

    def write_excel_sheet(
        self,
        relative_path: str,
        sheet_name: str,
        headers_json: str = "[]",
        rows_json: str = "[]",
    ) -> str:
        path = self._resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            workbook = _load_existing_workbook(path)
            try:
                worksheet = _get_sheet(workbook, sheet_name)
                _replace_sheet_rows(worksheet, _table_from_json(headers_json, rows_json))
                _save_workbook(workbook, path)
            except Exception:
                workbook.close()
                raise
        return str(path)

    def append_excel_rows(
        self,
        relative_path: str,
        rows_json: str,
        sheet_name: str = SHEET_NAME,
    ) -> str:
        path = self._resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            if not path.exists():
                self.create_excel_file(relative_path, sheet_name=sheet_name)
            workbook = _load_existing_workbook(path)
            try:
                worksheet = _get_sheet(workbook, sheet_name)
                for row in _parse_json_rows(rows_json, allow_flat=False):
                    worksheet.append(row)
                _save_workbook(workbook, path)
            except Exception:
                workbook.close()
                raise
        return str(path)

    def update_excel_cell(
        self,
        relative_path: str,
        cell: str,
        value: str,
        sheet_name: str = SHEET_NAME,
    ) -> str:
        path = self._resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            if not path.exists():
                self.create_excel_file(relative_path, sheet_name=sheet_name)
            workbook = _load_existing_workbook(path)
            try:
                worksheet = _get_sheet(workbook, sheet_name)
                old_value = worksheet[cell].value
                worksheet[cell] = value
                _save_workbook(workbook, path)
            except Exception:
                workbook.close()
                raise
        return f'{str(path)}:{sheet_name}:{cell} has been updated from "{old_value}" to "{value}"'

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


class CollectionWorkbookSink:
    """Persist page-local collection JSON into ``result.xlsx``."""

    def __init__(
        self,
        provider: ContentProvider | None = None,
        *,
        relative_path: str = "result.xlsx",
        reset: bool = True,
    ) -> None:
        self.provider = provider or ContentProvider()
        self.relative_path = relative_path
        self._merchant_sheets: dict[str, str] = {}
        self._item_signatures: dict[str, set[str]] = {}
        if reset:
            self.provider.create_excel_file(relative_path, sheet_name=SHEET_NAME)
        else:
            path = self.provider._resolve_excel_path(relative_path)
            if not path.exists():
                self.provider.create_excel_file(relative_path, sheet_name=SHEET_NAME)

    def persist_run_result(
        self,
        *,
        page_path: str,
        source_tool: str,
        run_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        if run_result.get("ok") is not True:
            return {"ok": True, "skipped": True, "reason": "collection_not_ok"}

        result = run_result.get("result")
        if not isinstance(result, Mapping):
            return {"ok": True, "skipped": True, "reason": "collection_result_not_object"}
        merchant = result.get("merchant")
        if not isinstance(merchant, Mapping):
            return {"ok": True, "skipped": True, "reason": "missing_merchant"}
        merchant_name = str(merchant.get("name") or "").strip()
        if not merchant_name:
            return {"ok": True, "skipped": True, "reason": "missing_merchant_name"}

        items = result.get("items")
        item_rows = [item for item in items if isinstance(item, Mapping)] if isinstance(items, list) else []
        append_items = source_tool in {"swipe_up", "swipe_down"}
        path = self.provider._resolve_excel_path(self.relative_path)

        with _lock_workbook_path(path):
            workbook = _load_existing_workbook(path) if path.exists() else Workbook()
            try:
                index_sheet = _ensure_sheet(workbook, SHEET_NAME)
                merchant_headers = _ensure_headers(index_sheet, merchant.keys())
                merchant_row = _upsert_row_by_key(index_sheet, merchant_headers, "name", merchant_name, merchant)

                merchant_sheet_name = self._ensure_merchant_sheet(workbook, merchant_name)
                item_headers = _ensure_headers_for_rows(workbook[merchant_sheet_name], item_rows)
                appended = self._append_unique_items(
                    workbook[merchant_sheet_name],
                    merchant_sheet_name,
                    item_headers,
                    item_rows if append_items else [],
                )
                skipped_items = len(item_rows) - appended if append_items else len(item_rows)
                _save_workbook(workbook, path)
            except Exception:
                workbook.close()
                raise

        return {
            "ok": True,
            "path": str(path),
            "merchant": merchant_name,
            "index_sheet": SHEET_NAME,
            "index_row": merchant_row,
            "merchant_sheet": merchant_sheet_name,
            "appended_items": appended,
            "skipped_items": skipped_items,
            "source_tool": source_tool,
        }

    def _ensure_merchant_sheet(self, workbook: OpenpyxlWorkbook, merchant_name: str) -> str:
        existing = self._merchant_sheets.get(merchant_name)
        if existing:
            if existing not in workbook.sheetnames:
                workbook.create_sheet(existing)
            return existing

        sheet_name = _unique_sheet_name(workbook, _sanitize_sheet_name(merchant_name))
        if sheet_name not in workbook.sheetnames:
            workbook.create_sheet(sheet_name)
        self._merchant_sheets[merchant_name] = sheet_name
        self._item_signatures.setdefault(sheet_name, set())
        return sheet_name

    def _append_unique_items(
        self,
        worksheet: Worksheet,
        sheet_name: str,
        headers: list[str],
        items: list[Mapping[str, Any]],
    ) -> int:
        signatures = self._item_signatures.setdefault(sheet_name, set())
        appended = 0
        for item in items:
            signature = _json_signature(item)
            if signature in signatures:
                continue
            signatures.add(signature)
            worksheet.append([_cell_value(item.get(header, "")) for header in headers])
            appended += 1
        return appended


def create_content_provider_tools(provider: ContentProvider | None = None) -> list[StructuredTool]:
    content_provider = provider or ContentProvider()
    return [
        StructuredTool.from_function(
            func=content_provider.create_excel_file,
            name="create_excel_file",
            description=(
                "Create an .xlsx file under workspace. Inputs: relative_path, optional "
                "headers_json, optional rows_json, and optional sheet_name."
            ),
        ),
        StructuredTool.from_function(
            func=content_provider.create_excel_sheet,
            name="create_excel_sheet",
            description=(
                "Create a worksheet in an existing .xlsx file. Inputs: relative_path, "
                "sheet_name, optional headers_json, and optional rows_json."
            ),
        ),
        StructuredTool.from_function(
            func=content_provider.rename_excel_sheet,
            name="rename_excel_sheet",
            description=(
                "Rename a worksheet. Inputs: relative_path, sheet_name, and new_sheet_name."
            ),
        ),
        StructuredTool.from_function(
            func=content_provider.write_excel_sheet,
            name="write_excel_sheet",
            description=(
                "Replace all rows in a specific worksheet. Inputs: relative_path, sheet_name, "
                "optional headers_json, and optional rows_json."
            ),
        ),
        StructuredTool.from_function(
            func=content_provider.append_excel_rows,
            name="append_excel_rows",
            description=(
                "Append rows to a specific worksheet. Inputs: relative_path, rows_json, "
                "and optional sheet_name."
            ),
        ),
        StructuredTool.from_function(
            func=content_provider.update_excel_cell,
            name="update_excel_cell",
            description=(
                "Update one cell in a specific worksheet. Inputs: relative_path, cell such "
                "as 'B2', value, and optional sheet_name."
            ),
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


def _table_from_json(headers_json: str, rows_json: str) -> list[list[object]]:
    headers = _parse_json_rows(headers_json, allow_flat=True)
    rows = _parse_json_rows(rows_json, allow_flat=False)
    table: list[list[object]] = []
    if headers:
        table.append(headers[0])
    table.extend(rows)
    return table


def _replace_sheet_rows(worksheet: Worksheet, rows: list[list[object]]) -> None:
    if worksheet.max_row:
        worksheet.delete_rows(1, worksheet.max_row)
    for row in rows:
        worksheet.append(row)


def _load_existing_workbook(path: Path) -> OpenpyxlWorkbook:
    if not path.exists():
        raise ValueError(f"Excel file does not exist: {path.name}")
    try:
        return load_workbook(path)
    except BadZipFile as exc:
        raise ValueError(
            f'Excel file "{path.name}" is corrupted and cannot be updated. '
            "Preserve it for inspection if needed, then recreate it with create_excel_file."
        ) from exc


def _save_workbook(workbook: OpenpyxlWorkbook, path: Path) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.stem}.",
            suffix=".tmp.xlsx",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)

        workbook.save(temporary_path)
        with ZipFile(temporary_path) as archive:
            corrupt_member = archive.testzip()
            if corrupt_member is not None:
                raise BadZipFile(f"Bad CRC-32 for file {corrupt_member!r}")
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        workbook.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@contextmanager
def _lock_workbook_path(path: Path) -> Iterator[None]:
    canonical_path = path.resolve()
    with _PATH_LOCKS_GUARD:
        path_lock = _PATH_LOCKS.setdefault(canonical_path, threading.RLock())
    with path_lock:
        yield


def _get_sheet(workbook: OpenpyxlWorkbook, sheet_name: str) -> Worksheet:
    validated_name = _validate_sheet_name(sheet_name)
    if validated_name not in workbook.sheetnames:
        available = ", ".join(workbook.sheetnames) or "none"
        raise ValueError(f'Worksheet "{validated_name}" does not exist. Available sheets: {available}')
    return workbook[validated_name]


def _ensure_sheet(workbook: OpenpyxlWorkbook, sheet_name: str) -> Worksheet:
    validated_name = _validate_sheet_name(sheet_name)
    if validated_name in workbook.sheetnames:
        return workbook[validated_name]
    return workbook.create_sheet(validated_name)


def _ensure_headers_for_rows(worksheet: Worksheet, rows: list[Mapping[str, Any]]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            normalized = str(key)
            if normalized not in seen:
                keys.append(normalized)
                seen.add(normalized)
    return _ensure_headers(worksheet, keys)


def _ensure_headers(worksheet: Worksheet, keys: Iterator[Any] | list[Any]) -> list[str]:
    headers = _current_headers(worksheet)
    for key in keys:
        normalized = str(key)
        if normalized and normalized not in headers:
            headers.append(normalized)
    for column_index, header in enumerate(headers, start=1):
        worksheet.cell(row=1, column=column_index, value=header)
    return headers


def _current_headers(worksheet: Worksheet) -> list[str]:
    if worksheet.max_row < 1:
        return []
    values = [value for value in next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), ())]
    headers = [str(value) if value is not None else "" for value in values]
    while headers and headers[-1] == "":
        headers.pop()
    if not any(headers):
        return []
    return headers


def _upsert_row_by_key(
    worksheet: Worksheet,
    headers: list[str],
    key: str,
    key_value: str,
    row_data: Mapping[str, Any],
) -> int:
    if key not in headers:
        headers = _ensure_headers(worksheet, [*headers, key])
    key_column = headers.index(key) + 1
    row_index = _find_row_by_value(worksheet, key_column, key_value)
    if row_index is None:
        row_index = max(worksheet.max_row + 1, 2)
    for column_index, header in enumerate(headers, start=1):
        if header in row_data or worksheet.cell(row=row_index, column=column_index).value is None:
            worksheet.cell(row=row_index, column=column_index, value=_cell_value(row_data.get(header, "")))
    return row_index


def _find_row_by_value(worksheet: Worksheet, column_index: int, value: str) -> int | None:
    for row_index in range(2, worksheet.max_row + 1):
        if str(worksheet.cell(row=row_index, column=column_index).value or "") == value:
            return row_index
    return None


def _validate_sheet_name(sheet_name: str) -> str:
    name = str(sheet_name or "")
    if not name.strip():
        raise ValueError("sheet_name must not be empty")
    if len(name) > 31:
        raise ValueError("sheet_name must not exceed 31 characters")
    if INVALID_SHEET_NAME_RE.search(name):
        raise ValueError(r"sheet_name must not contain any of: []:*?/\\")
    return name


def _ensure_unique_sheet_name(workbook: OpenpyxlWorkbook, sheet_name: str) -> None:
    if any(existing.casefold() == sheet_name.casefold() for existing in workbook.sheetnames):
        raise ValueError(f'Worksheet "{sheet_name}" already exists')


def _unique_sheet_name(workbook: OpenpyxlWorkbook, sheet_name: str) -> str:
    base = _sanitize_sheet_name(sheet_name)
    if not any(existing.casefold() == base.casefold() for existing in workbook.sheetnames):
        return base
    suffix = 2
    while True:
        marker = f"_{suffix}"
        candidate = f"{base[:31 - len(marker)]}{marker}"
        if not any(existing.casefold() == candidate.casefold() for existing in workbook.sheetnames):
            return candidate
        suffix += 1


def _sanitize_sheet_name(sheet_name: str) -> str:
    name = INVALID_SHEET_NAME_RE.sub("_", str(sheet_name or "")).strip()
    if not name:
        name = "merchant"
    return name[:31]


def _json_signature(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _cell_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _read_xlsx(path: Path, sheet_name: str = SHEET_NAME) -> list[list[object]]:
    with _lock_workbook_path(path):
        workbook = load_workbook(path, data_only=True)
        try:
            worksheet = _get_sheet(workbook, sheet_name)
            rows: list[list[object]] = []
            for row in worksheet.iter_rows(values_only=True):
                values = [_read_cell_value(value) for value in row]
                while values and values[-1] == "":
                    values.pop()
                rows.append(values)
            while rows and not rows[-1]:
                rows.pop()
            return rows
        finally:
            workbook.close()


def _read_cell_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    return str(value)
