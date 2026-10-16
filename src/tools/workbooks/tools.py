"""LangChain adapters for workspace Excel operations."""

from langchain_core.tools import StructuredTool

from src.tools.shared.factory import create_tool
from src.tools.workbooks.excel import ExcelWorkbookService


def create_content_provider_tools() -> list[StructuredTool]:
    content_provider: ExcelWorkbookService = ExcelWorkbookService()
    return [
        create_tool(
            func=content_provider.create_excel_file,
            name="create_excel_file",
            description=(
                "Create an .xlsx file under workspace. Inputs: relative_path, optional "
                "headers_json, optional rows_json, and optional sheet_name."
            ),
        ),
        create_tool(
            func=content_provider.create_excel_sheet,
            name="create_excel_sheet",
            description=(
                "Create a worksheet in an existing .xlsx file. Inputs: relative_path, "
                "sheet_name, optional headers_json, and optional rows_json."
            ),
        ),
        create_tool(
            func=content_provider.rename_excel_sheet,
            name="rename_excel_sheet",
            description=("Rename a worksheet. Inputs: relative_path, sheet_name, and new_sheet_name."),
        ),
        create_tool(
            func=content_provider.write_excel_sheet,
            name="write_excel_sheet",
            description=(
                "Replace all rows in a specific worksheet. Inputs: relative_path, sheet_name, "
                "optional headers_json, and optional rows_json."
            ),
        ),
        create_tool(
            func=content_provider.append_excel_rows,
            name="append_excel_rows",
            description=(
                "Append rows to a specific worksheet. Inputs: relative_path, rows_json, "
                "and optional sheet_name."
            ),
        ),
        create_tool(
            func=content_provider.update_excel_cell,
            name="update_excel_cell",
            description=(
                "Update one cell in a specific worksheet. Inputs: relative_path, cell such "
                "as 'B2', value, and optional sheet_name."
            ),
        ),
    ]
