from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict
from zipfile import BadZipFile

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from src.agent_runtime.coordinator import AgentCoordinator
from src.application.gateway_manager import GatewayManager
from src.application.model import build_model, close_model
from src.application.trace import trace_client
from src.channel.console import ConsoleChannel
from src.common.paths import WORKSPACE_ROOT
from src.common.utils.validation import is_string_mapping, is_value_sequence
from src.conductor.engine import ConductorEngine
from src.conductor.state import ConductorState
from src.configuration.manager import ConfigurationManager
from src.tools.workbooks.excel import ExcelReadValue
from src.tools.workbooks.paths import COLLECTION_WORKBOOK_NAME

WORKSPACE_DIR: Final[Path] = WORKSPACE_ROOT
RESULT_WORKBOOK_PATH: Final[Path] = WORKSPACE_DIR / COLLECTION_WORKBOOK_NAME
TRUTH_WORKBOOK_PATH: Final[Path] = WORKSPACE_DIR / "truth.xlsx"
MERCHANT_INDEX_SHEET: Final[str] = "Sheet1"
MERCHANT_INDEX_HEADERS: Final[tuple[str, str, str, str, str]] = (
    "merchant name",
    "distance",
    "rating",
    "total product count",
    "total review count",
)
MERCHANT_NAME_HEADER: Final[str] = "merchant name"
PRODUCT_DETAIL_HEADERS: Final[tuple[str, str, str, str]] = (
    "product name",
    "price",
    "original price",
    "discount price",
)
PRODUCT_NAME_HEADER: Final[str] = "product name"
SALES_HEADERS: Final[tuple[str, str]] = ("monthly sales", "sales")
TRUTH_PRODUCT_NAME_HEADERS: Final[tuple[str, str, str]] = ("product name", "product_name", "商品名")
# A collected product contributes +1 to result_count_grade. A false positive
# therefore cannot reduce the total score, while a correct product still raises it.
INCORRECT_PRODUCT_PENALTY: Final[int] = 1


class EvaluationDimensions(TypedDict):
    result_count_grade: int
    context_grade: int
    correctness_grade: int


class EvaluationResult(TypedDict):
    total: int
    dimensions: EvaluationDimensions
    merchant_count: int
    product_count: int
    incorrect_product_count: int


@dataclass(frozen=True)
class EvaluationReport:
    state: Mapping[str, object]


class PlainNotifier:
    def __call__(self, text: str) -> None:
        print(f"[NOTIFY] {text}")


def run() -> None:
    ConfigurationManager.initialize()
    trace_client()
    manager: GatewayManager = GatewayManager.instance()
    channel: ConsoleChannel = ConsoleChannel()
    gateway_id: str = manager.create(channel)
    model: BaseChatModel | None = None
    try:
        model = build_model()
        manager.start(gateway_id)
        channel.submit("执行应用探测任务")
        engine: ConductorEngine = ConductorEngine(
            model=model,
            gateway_id=gateway_id,
            session_id="main",
        )
        coordinator: AgentCoordinator[ConductorState] = AgentCoordinator(engine)
        try:
            print(coordinator.run())
        finally:
            coordinator.close()
    finally:
        manager.stop(gateway_id)
        manager.finish_output(gateway_id)
        manager.join(gateway_id)
        manager.release(gateway_id)
        if model is not None:
            close_model(model)


def eval(result: EvaluationReport) -> EvaluationResult:
    """Return a mind_controller-compatible structured evaluation."""

    result_merchants: dict[str, set[str]] = _read_merchant_products(RESULT_WORKBOOK_PATH)
    truth_merchants: dict[str, set[str]] = _read_truth_merchant_products(TRUTH_WORKBOOK_PATH)

    merchant_count: int = len(result_merchants)
    product_count: int = sum(len(products) for products in result_merchants.values())
    result_count_grade: int = merchant_count + product_count
    context_grade: int = _context_grade(result)
    incorrect_product_count: int = _incorrect_product_count(result_merchants, truth_merchants)
    correctness_grade: int = -INCORRECT_PRODUCT_PENALTY * incorrect_product_count
    total: int = result_count_grade + context_grade + correctness_grade

    return {
        "total": total,
        "dimensions": {
            "result_count_grade": result_count_grade,
            "context_grade": context_grade,
            "correctness_grade": correctness_grade,
        },
        "merchant_count": merchant_count,
        "product_count": product_count,
        "incorrect_product_count": incorrect_product_count,
    }


def _read_merchant_products(path: Path) -> dict[str, set[str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Evaluation workbook does not exist: {path}")

    try:
        workbook: Workbook = load_workbook(path, data_only=False)
    except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
        raise ValueError(f"Unable to read evaluation workbook {path}: {exc}") from exc

    merchants: dict[str, set[str]] = {}
    try:
        if MERCHANT_INDEX_SHEET not in workbook.sheetnames:
            raise ValueError(
                f"Evaluation workbook {path} must contain merchant index worksheet {MERCHANT_INDEX_SHEET!r}"
            )
        index_worksheet: Worksheet = workbook[MERCHANT_INDEX_SHEET]
        header_row_number, headers = _read_headers(
            index_worksheet,
            path,
            MERCHANT_INDEX_HEADERS,
        )
        merchant_index: int = headers.index(MERCHANT_NAME_HEADER)
        for row in index_worksheet.iter_rows(min_row=header_row_number + 1):
            merchant_name: str = _merchant_name(row, merchant_index, path)
            if not merchant_name:
                continue
            detail_worksheet: Worksheet = _merchant_worksheet(
                workbook,
                merchant_name,
                path,
            )
            products: set[str] = _read_product_worksheet(detail_worksheet, path)
            merchants.setdefault(merchant_name, set()).update(products)
    finally:
        workbook.close()
    return merchants


def _read_headers(
    worksheet: Worksheet,
    workbook_path: Path,
    required_headers: Sequence[str],
) -> tuple[int, list[str]]:
    header_row: tuple[Cell | MergedCell, ...] | None = None
    for row in worksheet.iter_rows():
        if any(_normalized_name(cell.value) for cell in row):
            header_row = row
            break
    if header_row is None:
        raise ValueError(f"Worksheet {worksheet.title!r} in {workbook_path} is empty")

    headers: list[str] = [_normalized_name(cell.value) for cell in header_row]
    nonempty_headers: list[str] = [header for header in headers if header]
    missing_headers: list[str] = [header for header in required_headers if header not in headers]
    if missing_headers:
        raise ValueError(
            f"Worksheet {worksheet.title!r} in {workbook_path} "
            f"is missing required headers: {missing_headers!r}"
        )
    if len(set(nonempty_headers)) != len(nonempty_headers):
        raise ValueError(f"Worksheet {worksheet.title!r} in {workbook_path} contains duplicate headers")
    row_number: int | None = header_row[0].row
    if row_number is None:
        raise ValueError("Header row has no worksheet position")
    return row_number, headers


def _merchant_worksheet(
    workbook: Workbook,
    merchant_name: str,
    workbook_path: Path,
) -> Worksheet:
    matching_names: list[str] = [
        worksheet_name
        for worksheet_name in workbook.sheetnames
        if worksheet_name != MERCHANT_INDEX_SHEET and _normalized_name(worksheet_name) == merchant_name
    ]
    if not matching_names:
        raise ValueError(
            f"Merchant {merchant_name!r} in worksheet {MERCHANT_INDEX_SHEET!r} "
            f"of {workbook_path} must have a product worksheet with the same name"
        )
    if len(matching_names) > 1:
        raise ValueError(f"Merchant {merchant_name!r} in {workbook_path} matches multiple product worksheets")
    return workbook[matching_names[0]]


def _merchant_name(
    row: Sequence[Cell | MergedCell],
    merchant_index: int,
    workbook_path: Path,
) -> str:
    if merchant_index >= len(row):
        return ""
    merchant_cell: Cell | MergedCell = row[merchant_index]
    if merchant_cell.data_type == "f":
        raise ValueError(
            f"Merchant cell {merchant_cell.coordinate} in {workbook_path} "
            "must contain plain text, not a formula"
        )
    return _normalized_name(merchant_cell.value)


def _read_product_worksheet(worksheet: Worksheet, workbook_path: Path) -> set[str]:
    header_row_number, headers = _read_headers(
        worksheet,
        workbook_path,
        PRODUCT_DETAIL_HEADERS,
    )
    if not any(header in headers for header in SALES_HEADERS):
        raise ValueError(
            f"Worksheet {worksheet.title!r} in {workbook_path} is missing required "
            f"sales header; expected one of {SALES_HEADERS!r}"
        )
    product_index: int = headers.index(PRODUCT_NAME_HEADER)
    products: set[str] = set()
    for row in worksheet.iter_rows(
        min_row=header_row_number + 1,
        values_only=True,
    ):
        product_name: str = _row_name(row, product_index)
        if product_name:
            products.add(product_name)
    return products


def _read_truth_merchant_products(path: Path) -> dict[str, set[str]]:
    if not path.is_file():
        raise FileNotFoundError(f"Evaluation workbook does not exist: {path}")

    try:
        workbook: Workbook = load_workbook(path, read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
        raise ValueError(f"Unable to read evaluation workbook {path}: {exc}") from exc

    merchants: dict[str, set[str]] = {}
    try:
        if MERCHANT_INDEX_SHEET in workbook.sheetnames:
            index_headers: set[str] = {
                _normalized_name(cell.value)
                for row in workbook[MERCHANT_INDEX_SHEET].iter_rows(max_row=1)
                for cell in row
            }
            if set(MERCHANT_INDEX_HEADERS) <= index_headers:
                workbook.close()
                return _read_merchant_products(path)
        for worksheet in workbook.worksheets:
            rows: Iterator[tuple[ExcelReadValue, ...]] = iter(worksheet.iter_rows(values_only=True))
            header_row: tuple[ExcelReadValue, ...] | None = next(
                (row for row in rows if any(_normalized_name(value) for value in row)),
                None,
            )
            if header_row is None:
                continue
            headers: list[str] = [_normalized_name(value) for value in header_row]
            product_headers: list[str] = [
                header for header in TRUTH_PRODUCT_NAME_HEADERS if header in headers
            ]
            if len(product_headers) != 1:
                raise ValueError(
                    f"Worksheet {worksheet.title!r} in {path} must contain exactly "
                    f"one product header from {TRUTH_PRODUCT_NAME_HEADERS!r}"
                )
            product_index: int = headers.index(product_headers[0])
            products: set[str] = {
                product_name for row in rows if (product_name := _row_name(row, product_index))
            }
            merchant_name: str = _normalized_name(worksheet.title)
            merchants.setdefault(merchant_name, set()).update(products)
    finally:
        workbook.close()
    return merchants


def _row_name(row: Sequence[ExcelReadValue], index: int) -> str:
    return _normalized_name(row[index]) if index < len(row) else ""


def _normalized_name(value: ExcelReadValue) -> str:
    return "" if value is None else str(value).strip()


def _context_grade(result: EvaluationReport) -> int:
    state: object = result.state
    if not is_string_mapping(state):
        raise ValueError("EvaluationReport.state must be a mapping")
    messages: object = state.get("messages")
    if not is_value_sequence(messages):
        raise ValueError("EvaluationReport.state['messages'] must be a sequence")
    if any(not isinstance(message, BaseMessage) for message in messages):
        raise ValueError("EvaluationReport.state['messages'] must contain BaseMessage values")

    current_path: str = ""
    operation_count: int = 0
    redundant_operation_count: int = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for tool_call in message.tool_calls:
            tool_name: str = str(tool_call.get("name") or "")
            args: object = tool_call["args"]
            if tool_name == "notify_user":
                if not is_string_mapping(args):
                    raise ValueError("notify_user tool arguments must be a mapping")
                raw_path: object = args.get("current_path")
                if raw_path is not None and not isinstance(raw_path, str):
                    raise ValueError("notify_user current_path must be text")
                current_path = _normalized_name(raw_path)
                continue
            operation_count += 1
            if _is_merchant_path(current_path):
                redundant_operation_count += 1

    if operation_count == 0:
        return 0
    return round(100 * (1 - redundant_operation_count / operation_count))


def _is_merchant_path(path: str) -> bool:
    normalized: str = path.strip().rstrip("/")
    return normalized == "商家" or normalized.endswith("/商家")


def _incorrect_product_count(
    result_merchants: Mapping[str, set[str]],
    truth_merchants: Mapping[str, set[str]],
) -> int:
    shared_merchants: set[str] = result_merchants.keys() & truth_merchants.keys()
    return sum(len(result_merchants[merchant] - truth_merchants[merchant]) for merchant in shared_merchants)
