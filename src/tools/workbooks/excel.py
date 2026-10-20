"""Workspace-scoped Excel output tools."""

from __future__ import annotations

import json
import os
import posixpath
import re
import tempfile
import threading
from collections.abc import Callable, Generator, Iterable, Mapping
from contextlib import contextmanager
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Final
from zipfile import BadZipFile, ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.cell.rich_text import CellRichText
from openpyxl.workbook.workbook import Workbook as OpenpyxlWorkbook
from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
from openpyxl.worksheet.worksheet import Worksheet

from src.common.paths import WORKSPACE_ROOT
from src.common.utils.json import JsonObject, JsonScalar, JsonValue, load_json
from src.page.collection_contract import CollectionContract
from src.page.script_types import ScriptResult
from src.tools.workbooks.paths import COLLECTION_WORKBOOK_NAME
from src.tools.workbooks.types import PersistenceResult

type ExcelReadValue = (
    str
    | int
    | float
    | bool
    | Decimal
    | date
    | time
    | timedelta
    | CellRichText
    | ArrayFormula
    | DataTableFormula
    | None
)

SHEET_NAME: Final[str] = "Sheet1"
INVALID_SHEET_NAME_RE: Final[re.Pattern[str]] = re.compile(r"[\[\]:*?/\\]")
_PATH_LOCKS: dict[Path, threading.RLock] = {}
_PATH_LOCKS_GUARD: Final[threading.Lock] = threading.Lock()


class ExcelWorkbookService:
    """Create and update Excel files under the workspace directory."""

    def __init__(self) -> None:
        self.workspace_root: Path = WORKSPACE_ROOT.resolve()
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def create_excel_file(
        self,
        relative_path: str,
        headers_json: str = "[]",
        rows_json: str = "[]",
        sheet_name: str = SHEET_NAME,
    ) -> str:
        path: Path = self.resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            validated_name: str = _validate_sheet_name(sheet_name)
            workbook: Workbook = Workbook()
            worksheet: Worksheet = _get_sheet(workbook, workbook.sheetnames[0])
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
        path: Path = self.resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            workbook: Workbook = _load_existing_workbook(path)
            try:
                validated_name: str = _validate_sheet_name(sheet_name)
                _ensure_unique_sheet_name(workbook, validated_name)
                worksheet: Worksheet = workbook.create_sheet(validated_name)
                _replace_sheet_rows(worksheet, _table_from_json(headers_json, rows_json))
                _save_workbook(workbook, path)
            except Exception:
                workbook.close()
                raise
        return str(path)

    def rename_excel_sheet(self, relative_path: str, sheet_name: str, new_sheet_name: str) -> str:
        path: Path = self.resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            workbook: Workbook = _load_existing_workbook(path)
            try:
                worksheet: Worksheet = _get_sheet(workbook, sheet_name)
                validated_name: str = _validate_sheet_name(new_sheet_name)
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
        path: Path = self.resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            workbook: Workbook = _load_existing_workbook(path)
            try:
                worksheet: Worksheet = _get_sheet(workbook, sheet_name)
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
        path: Path = self.resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            if not path.exists():
                self.create_excel_file(relative_path, sheet_name=sheet_name)
            workbook: Workbook = _load_existing_workbook(path)
            try:
                worksheet: Worksheet = _get_sheet(workbook, sheet_name)
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
        path: Path = self.resolve_excel_path(relative_path)
        with _lock_workbook_path(path):
            if not path.exists():
                self.create_excel_file(relative_path, sheet_name=sheet_name)
            workbook: Workbook = _load_existing_workbook(path)
            try:
                worksheet: Worksheet = _get_sheet(workbook, sheet_name)
                old_value: ExcelReadValue = worksheet[cell].value
                worksheet[cell] = value
                _save_workbook(workbook, path)
            except Exception:
                workbook.close()
                raise
        return f'{str(path)}:{sheet_name}:{cell} has been updated from "{old_value}" to "{value}"'

    def resolve_excel_path(self, relative_path: str) -> Path:
        normalized: str = posixpath.normpath(str(relative_path or "").replace("\\", "/")).lstrip("/")
        if normalized in {"", "."}:
            raise ValueError("relative_path is required")
        path: Path = (self.workspace_root / normalized).resolve()
        if self.workspace_root != path and self.workspace_root not in path.parents:
            raise ValueError("Excel path must stay inside workspace")
        if path.suffix.lower() != ".xlsx":
            raise ValueError("Excel path must end with .xlsx")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


class CollectionWorkbookSink:
    """Persist page-local collection JSON into ``result.xlsx``."""

    def __init__(self) -> None:
        self.provider: ExcelWorkbookService = ExcelWorkbookService()
        self.relative_path: str = COLLECTION_WORKBOOK_NAME
        self._merchant_sheets: dict[str, str] = {}
        self.provider.create_excel_file(self.relative_path, sheet_name=SHEET_NAME)

    def persist_run_result(
        self,
        *,
        run_result: ScriptResult,
        append_items: bool,
        contract: CollectionContract | None = None,
    ) -> PersistenceResult:
        if run_result["ok"] is not True:
            return {"ok": True, "skipped": True, "reason": "collection_not_ok"}

        result: JsonObject = run_result["result"]
        merchant: JsonValue = result.get("merchant")
        if not isinstance(merchant, dict):
            return {"ok": True, "skipped": True, "reason": "missing_merchant"}
        name_value: JsonValue = merchant.get("name")
        merchant_name: str = name_value.strip() if isinstance(name_value, str) else ""
        if not merchant_name:
            return {"ok": True, "skipped": True, "reason": "missing_merchant_name"}

        items: JsonValue = result.get("items")
        item_rows: list[JsonObject] = (
            [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
        )
        path: Path = self.provider.resolve_excel_path(self.relative_path)

        with _lock_workbook_path(path):
            workbook: Workbook = _load_existing_workbook(path) if path.exists() else Workbook()
            try:
                if not self._merchant_sheets and SHEET_NAME in workbook.sheetnames:
                    rows: list[tuple[ExcelReadValue, ...]] = list(workbook[SHEET_NAME].values)
                    if rows and "name" in rows[0]:
                        name_column: int = rows[0].index("name")
                        for row, name in zip(rows[1:], [s for s in workbook.sheetnames if s != SHEET_NAME]):
                            stored_name: ExcelReadValue = row[name_column]
                            if isinstance(stored_name, str):
                                self._merchant_sheets[stored_name] = name
                index_sheet: Worksheet = _ensure_sheet(workbook, SHEET_NAME)
                merchant_headers: list[str] = _ensure_headers(index_sheet, merchant.keys())
                merchant_row: int = _upsert_row_by_key(
                    index_sheet, merchant_headers, "name", merchant_name, merchant
                )

                merchant_sheet_name: str = self._ensure_merchant_sheet(workbook, merchant_name)
                item_headers: list[str] = _ensure_headers_for_rows(workbook[merchant_sheet_name], item_rows)
                appended: int = self._append_unique_items(
                    workbook[merchant_sheet_name],
                    item_headers,
                    item_rows if append_items else [],
                    contract=contract,
                )
                skipped_items: int = len(item_rows) - appended if append_items else len(item_rows)
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
        }

    def _ensure_merchant_sheet(self, workbook: OpenpyxlWorkbook, merchant_name: str) -> str:
        existing: str | None = self._merchant_sheets.get(merchant_name)
        if existing:
            if existing not in workbook.sheetnames:
                workbook.create_sheet(existing)
            return existing

        sheet_name: str = _unique_sheet_name(workbook, _sanitize_sheet_name(merchant_name))
        if sheet_name not in workbook.sheetnames:
            workbook.create_sheet(sheet_name)
        self._merchant_sheets[merchant_name] = sheet_name
        return sheet_name

    def _append_unique_items(
        self,
        worksheet: Worksheet,
        headers: list[str],
        items: list[JsonObject],
        contract: CollectionContract | None = None,
    ) -> int:
        signature_of: Callable[[Mapping[str, JsonValue]], str | tuple[str, ...]] = (
            contract.key if contract is not None else _json_signature
        )
        signatures: set[str | tuple[str, ...]] = {
            signature_of({key: _stored_json_value(value) for key, value in zip(headers, row)})
            for row in worksheet.iter_rows(min_row=2, values_only=True)
        }
        appended: int = 0
        for item in items:
            if contract and contract.missing(item):
                continue
            signature: str | tuple[str, ...] = signature_of(item)
            if signature in signatures:
                continue
            signatures.add(signature)
            worksheet.append([_cell_value(item.get(header, "")) for header in headers])
            appended += 1
        return appended


def _parse_json_rows(value: str, allow_flat: bool) -> list[list[JsonValue]]:
    if not value:
        return []
    parsed: JsonValue = load_json(value)
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
    rows: list[list[JsonValue]] = []
    for item in parsed:
        if isinstance(item, dict):
            rows.append([item[key] for key in item])
        elif isinstance(item, list):
            rows.append(item)
        else:
            rows.append([item])
    return rows


def _is_row_like(value: JsonValue) -> bool:
    return isinstance(value, list | dict)


def _table_from_json(headers_json: str, rows_json: str) -> list[list[JsonValue]]:
    headers: list[list[JsonValue]] = _parse_json_rows(headers_json, allow_flat=True)
    rows: list[list[JsonValue]] = _parse_json_rows(rows_json, allow_flat=False)
    table: list[list[JsonValue]] = []
    if headers:
        table.append(headers[0])
    table.extend(rows)
    return table


def _replace_sheet_rows(worksheet: Worksheet, rows: list[list[JsonValue]]) -> None:
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
            corrupt_member: str | None = archive.testzip()
            if corrupt_member is not None:
                raise BadZipFile(f"Bad CRC-32 for file {corrupt_member!r}")
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        workbook.close()
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@contextmanager
def _lock_workbook_path(path: Path) -> Generator[None]:
    canonical_path: Path = path.resolve()
    with _PATH_LOCKS_GUARD:
        path_lock: threading.RLock = _PATH_LOCKS.setdefault(canonical_path, threading.RLock())
    with path_lock:
        yield


def _get_sheet(workbook: OpenpyxlWorkbook, sheet_name: str) -> Worksheet:
    validated_name: str = _validate_sheet_name(sheet_name)
    if validated_name not in workbook.sheetnames:
        available: str = ", ".join(workbook.sheetnames) or "none"
        raise ValueError(f'Worksheet "{validated_name}" does not exist. Available sheets: {available}')
    return workbook[validated_name]


def _ensure_sheet(workbook: OpenpyxlWorkbook, sheet_name: str) -> Worksheet:
    validated_name: str = _validate_sheet_name(sheet_name)
    if validated_name in workbook.sheetnames:
        return workbook[validated_name]
    return workbook.create_sheet(validated_name)


def _ensure_headers_for_rows(worksheet: Worksheet, rows: list[JsonObject]) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            normalized: str = str(key)
            if normalized not in seen:
                keys.append(normalized)
                seen.add(normalized)
    return _ensure_headers(worksheet, keys)


def _ensure_headers(worksheet: Worksheet, keys: Iterable[str]) -> list[str]:
    headers: list[str] = _current_headers(worksheet)
    for key in keys:
        normalized: str = str(key)
        if normalized and normalized not in headers:
            headers.append(normalized)
    for column_index, header in enumerate(headers, start=1):
        worksheet.cell(row=1, column=column_index, value=header)
    return headers


def _current_headers(worksheet: Worksheet) -> list[str]:
    if worksheet.max_row < 1:
        return []
    values: list[ExcelReadValue] = [
        value for value in next(worksheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    ]
    headers: list[str] = [str(value) if value is not None else "" for value in values]
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
    row_data: Mapping[str, JsonValue],
) -> int:
    if key not in headers:
        headers = _ensure_headers(worksheet, [*headers, key])
    key_column: int = headers.index(key) + 1
    row_index: int | None = _find_row_by_value(worksheet, key_column, key_value)
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
    name: str = str(sheet_name or "")
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
    base: str = _sanitize_sheet_name(sheet_name)
    if not any(existing.casefold() == base.casefold() for existing in workbook.sheetnames):
        return base
    suffix: int = 2
    while True:
        marker: str = f"_{suffix}"
        candidate: str = f"{base[: 31 - len(marker)]}{marker}"
        if not any(existing.casefold() == candidate.casefold() for existing in workbook.sheetnames):
            return candidate
        suffix += 1


def _sanitize_sheet_name(sheet_name: str) -> str:
    name: str = INVALID_SHEET_NAME_RE.sub("_", str(sheet_name or "")).strip()
    if not name:
        name = "merchant"
    return name[:31]


def _json_signature(value: Mapping[str, JsonValue]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _cell_value(value: JsonValue) -> str:
    if value is None:
        return ""
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def _stored_json_value(value: ExcelReadValue) -> JsonScalar:
    """Normalize openpyxl values at the workbook input boundary."""
    if value is None or isinstance(value, str | bool | int | float):
        return value
    return str(value)
