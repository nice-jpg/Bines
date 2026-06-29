from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from agent import AgentRunResult, AgentRuntime
from langchain_core.messages import AIMessage, BaseMessage
from model import build_codex_model
from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from tools.common import CaptchaAuthenticationTool, OperationNoticeTool, create_common_tools


AgentRunLoop = Callable[[], AgentRunResult]
Evaluator = Callable[[AgentRunResult], Mapping[str, Any]]
WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "workspace"
RESULT_WORKBOOK_PATH = WORKSPACE_DIR / "result.xlsx"
TRUTH_WORKBOOK_PATH = WORKSPACE_DIR / "truth.xlsx"
MERCHANT_NAME_HEADER = "merchant_name"
PRODUCT_NAME_HEADERS = ("product_name", "商品名")
INCORRECT_PRODUCT_PENALTY = 10


class PlainNotifier:
    def set(self, _) -> None:
        pass

    def __call__(self, text: str) -> None:
        print(f"[NOTIFY] {text}")


def run() -> None:
    notifier = PlainNotifier()
    operation_notice = OperationNoticeTool(notifier=notifier)
    captcha_authentication = CaptchaAuthenticationTool(notifier=notifier)
    runtime = AgentRuntime(
        model=build_codex_model(),
        tools=create_common_tools(
            operation_notice_tool=operation_notice,
            captcha_authentication_tool=captcha_authentication,
        ),
        name="main",
    )

    r = runtime.run_turn(
        [{"role": "user", "content": "执行应用探测任务"}],
        session_id="main",
        max_iterations=1000,
    )

    print(r)


def eval(result: AgentRunResult) -> Mapping[str, Any]:
    """Return a mind_controller-compatible structured evaluation."""

    result_merchants = _read_merchant_products(RESULT_WORKBOOK_PATH)
    truth_merchants = _read_merchant_products(TRUTH_WORKBOOK_PATH)

    merchant_count = len(result_merchants)
    product_count = sum(len(products) for products in result_merchants.values())
    result_count_grade = merchant_count + product_count
    context_grade = _context_grade(result)
    incorrect_product_count = _incorrect_product_count(result_merchants, truth_merchants)
    correctness_grade = -INCORRECT_PRODUCT_PENALTY * incorrect_product_count
    total = result_count_grade + context_grade + correctness_grade

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
        workbook = load_workbook(path, read_only=True, data_only=True)
    except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
        raise ValueError(f"Unable to read evaluation workbook {path}: {exc}") from exc

    merchants: dict[str, set[str]] = {}
    try:
        for worksheet in workbook.worksheets:
            parsed = _read_merchant_worksheet(worksheet, path)
            if parsed is None:
                continue
            merchant_name, products = parsed
            merchants.setdefault(merchant_name, set()).update(products)
    finally:
        workbook.close()
    return merchants


def _read_merchant_worksheet(
    worksheet: Any,
    workbook_path: Path,
) -> tuple[str, set[str]] | None:
    rows = iter(worksheet.iter_rows(values_only=True))
    header_row: tuple[Any, ...] | None = None
    for row in rows:
        if any(_normalized_name(value) for value in row):
            header_row = row
            break
    if header_row is None:
        return None

    headers = [_normalized_name(value) for value in header_row]
    nonempty_headers = [header for header in headers if header]
    product_headers = [header for header in PRODUCT_NAME_HEADERS if header in headers]
    if not product_headers:
        raise ValueError(
            f"Worksheet {worksheet.title!r} in {workbook_path} "
            f"must contain exactly one product header from {PRODUCT_NAME_HEADERS!r}"
        )
    if len(product_headers) > 1:
        raise ValueError(
            f"Worksheet {worksheet.title!r} in {workbook_path} contains multiple "
            f"product headers: {product_headers!r}"
        )
    if len(set(nonempty_headers)) != len(nonempty_headers):
        raise ValueError(
            f"Worksheet {worksheet.title!r} in {workbook_path} contains duplicate headers"
        )

    product_index = headers.index(product_headers[0])
    merchant_index = (
        headers.index(MERCHANT_NAME_HEADER) if MERCHANT_NAME_HEADER in headers else None
    )
    merchant_names: set[str] = set()
    products: set[str] = set()
    for row in rows:
        if merchant_index is not None:
            merchant_name = _row_name(row, merchant_index)
            if merchant_name:
                merchant_names.add(merchant_name)
        product_name = _row_name(row, product_index)
        if product_name:
            products.add(product_name)

    if len(merchant_names) > 1:
        raise ValueError(
            f"Worksheet {worksheet.title!r} in {workbook_path} contains multiple "
            f"merchant_name values: {sorted(merchant_names)!r}"
        )
    merchant_name = next(iter(merchant_names), _normalized_name(worksheet.title))
    if not merchant_name:
        raise ValueError(
            f"Worksheet {worksheet.title!r} in {workbook_path} has no merchant name"
        )
    return merchant_name, products


def _row_name(row: Sequence[Any], index: int) -> str:
    return _normalized_name(row[index]) if index < len(row) else ""


def _normalized_name(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _context_grade(result: AgentRunResult) -> int:
    state = getattr(result, "state", None)
    if not isinstance(state, Mapping):
        raise ValueError("AgentRunResult.state must be a mapping")
    messages = state.get("messages")
    if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes)):
        raise ValueError("AgentRunResult.state['messages'] must be a sequence")
    if any(not isinstance(message, BaseMessage) for message in messages):
        raise ValueError("AgentRunResult.state['messages'] must contain BaseMessage values")

    current_path = ""
    operation_count = 0
    redundant_operation_count = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for tool_call in message.tool_calls:
            if not isinstance(tool_call, Mapping):
                raise ValueError("AIMessage.tool_calls must contain mapping values")
            tool_name = str(tool_call.get("name") or "")
            args = tool_call.get("args") or {}
            if tool_name == "notify_user":
                if not isinstance(args, Mapping):
                    raise ValueError("notify_user tool arguments must be a mapping")
                current_path = _normalized_name(args.get("current_path"))
                continue
            operation_count += 1
            if _is_merchant_path(current_path):
                redundant_operation_count += 1

    if operation_count == 0:
        return 0
    return round(100 * (1 - redundant_operation_count / operation_count))


def _is_merchant_path(path: str) -> bool:
    normalized = path.strip().rstrip("/")
    return normalized == "商家" or normalized.endswith("/商家")


def _incorrect_product_count(
    result_merchants: Mapping[str, set[str]],
    truth_merchants: Mapping[str, set[str]],
) -> int:
    shared_merchants = result_merchants.keys() & truth_merchants.keys()
    return sum(
        len(result_merchants[merchant] - truth_merchants[merchant])
        for merchant in shared_merchants
    )
