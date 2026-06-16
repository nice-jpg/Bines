"""Common LangChain tools shared by the Bines agent."""

from __future__ import annotations

from pathlib import Path
import posixpath

from langchain_core.tools import StructuredTool

PAGE_MECHANISM_DIR = Path(__file__).resolve().parents[1] / "prompts" / "page_mechanism"


class ThinkingTool:
    """No-op scratchpad tool for reflecting on complex tool results."""

    def think(self, thought: str) -> str:
        """Record a thought without changing external state or fetching data."""

        if not str(thought or "").strip():
            return "No thought was recorded because the input was empty."
        return "Thought recorded."


class ManualTool:
    """Read page operation manuals from the page_mechanism directory."""

    def __init__(self, page_mechanism_dir: str | Path = PAGE_MECHANISM_DIR) -> None:
        self.page_mechanism_dir = Path(page_mechanism_dir).resolve()

    def query_manual(self, current_path: str) -> str:
        """Return PAGE.md content for the current app or operation path."""

        parts = _normalize_manual_path(current_path)
        if not parts:
            return "No manual path was provided."

        doc = self._manual_doc_for_path(parts)
        if doc is None:
            return f"No manual found for path: {'/'.join(parts)}"

        lines = ["<manual_context>"]
        relative_path = doc.relative_to(self.page_mechanism_dir)
        lines.append(f"<page path=\"{relative_path.as_posix()}\">")
        lines.append(doc.read_text(encoding="utf-8"))
        lines.append("</page>")
        lines.append("</manual_context>")
        return "\n".join(lines)

    def _manual_doc_for_path(self, parts: list[str]) -> Path | None:
        app_slug = _app_slug(parts[0])
        path_parts = [app_slug, *parts[1:]]
        candidate = self.page_mechanism_dir.joinpath(*path_parts, "PAGE.md").resolve()
        if not _is_relative_to(candidate, self.page_mechanism_dir):
            return None
        if candidate.exists():
            return candidate
        return None


def create_common_tools(
    thinking_tool: ThinkingTool | None = None,
    manual_tool: ManualTool | None = None,
) -> list[StructuredTool]:
    """Create shared reasoning tools for the LangChain agent."""

    thinking = thinking_tool or ThinkingTool()
    manual = manual_tool or ManualTool()
    return [
        StructuredTool.from_function(
            func=thinking.think,
            name="think",
            description=(
                "Use this tool as a private scratchpad after receiving complex tool outputs. "
                "It does not fetch new information, operate the device, write files, or change "
                "any external state. Use it to analyze the latest result, check whether required "
                "information is complete, identify risks or contradictions, and plan the next "
                "device or output action. Input: thought."
            ),
        ),
        StructuredTool.from_function(
            func=manual.query_manual,
            name="query_manual",
            description=(
                "Read app/page operation guidance. Input current_path as an "
                "application name or operation path, such as 'com.sankuai.meituan', 'meituan/外卖', "
                "or 'meituan/外卖/商家'. The tool returns only the PAGE.md manual for the "
                "current page path."
            ),
        ),
    ]


def build_tools() -> list[StructuredTool]:
    """Return the default common tools."""

    return create_common_tools()


def _normalize_manual_path(current_path: str) -> list[str]:
    normalized = posixpath.normpath(str(current_path or "").replace("\\", "/")).strip("/")
    if normalized in {"", "."} or normalized.startswith("../") or normalized == "..":
        return []
    return [part for part in normalized.split("/") if part and part != "."]


def _app_slug(application_name: str) -> str:
    normalized = application_name.strip()
    aliases = {
        "com.sankuai.meituan": "meituan",
        "美团": "meituan",
        "meituan": "meituan",
    }
    return aliases.get(normalized, normalized)


def _is_relative_to(path: Path, root: Path) -> bool:
    return path == root or root in path.parents
