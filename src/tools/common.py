"""Common LangChain tools shared by the Bines agent."""

from __future__ import annotations

from collections import deque
import json
from pathlib import Path
import posixpath
from typing import Any, Callable

from langchain_core.tools import StructuredTool

try:
    from src.tools.event_logger import WorkspaceEventLogger
except ModuleNotFoundError:  # Supports running tests with src on sys.path.
    from tools.event_logger import WorkspaceEventLogger

PAGE_MECHANISM_DIR = Path(__file__).resolve().parents[1] / "prompts" / "page_mechanism"
SHADOW_AUTH_URL = "http://139.224.44.6:9080"


class OperationNoticeTool:
    """Record user-visible operation notices before external actions."""

    def __init__(
        self,
        logger: WorkspaceEventLogger | None = None,
        max_entries: int = 30,
        notifier: Callable[[str], None] | None = None,
    ) -> None:
        self.logger = logger or WorkspaceEventLogger()
        self.max_entries = max(1, int(max_entries))
        self.notifier = notifier
        self._entries: deque[dict[str, str]] = deque(maxlen=self.max_entries)

    def notify_user(
        self,
        operation: str,
        current_path: str,
        reason: str = "",
        next_page: str = "",
        next_path: str = "",
    ) -> str:
        """Record the next external operation and return recent operation context."""

        normalized_operation = str(operation or "").strip()
        if not normalized_operation:
            return "No operation notice was recorded because the operation input was empty."

        entry = {
            "operation": normalized_operation,
            "current_path": str(current_path or "").strip(),
            "reason": str(reason or "").strip(),
            "next_page": str(next_page or "").strip(),
            "next_path": str(next_path or "").strip(),
        }
        self._entries.append(entry)
        self.logger.log(
            "operation_notice",
            entry["operation"],
            details={
                "current_path": entry["current_path"],
                "reason": entry["reason"],
                "next_page": entry["next_page"],
                "next_path": entry["next_path"],
            },
        )
        self._notify(entry)
        return self._format_log()

    def _notify(self, entry: dict[str, str]) -> None:
        if self.notifier is None:
            return
        lines = [f"Operation: {entry['operation']}"]
        if entry["current_path"]:
            lines.append(f"Current path: {entry['current_path']}")
        if entry["reason"]:
            lines.append(f"Reason: {entry['reason']}")
        if entry["next_page"] or entry["next_path"]:
            lines.append(f"Next page: {entry['next_page'] or '-'}")
            lines.append(f"Next path: {entry['next_path'] or '-'}")
        try:
            self.notifier("\n".join(lines))
        except Exception as exc:  # noqa: BLE001 - communication errors must not interrupt tools.
            self.logger.log(
                "operation_notice_delivery_error",
                "Failed to deliver operation notice.",
                details={"error": str(exc), "operation": entry["operation"]},
            )

    def _format_log(self) -> str:
        lines = ["<operation_log>"]
        lines.append(f"<recent_entries count=\"{len(self._entries)}\" max=\"{self.max_entries}\">")
        for index, entry in enumerate(self._entries, start=1):
            lines.append(
                "<entry "
                f"index=\"{index}\" "
                f"current_path=\"{_escape_attr(entry['current_path'])}\" "
                f"next_page=\"{_escape_attr(entry['next_page'])}\" "
                f"next_path=\"{_escape_attr(entry['next_path'])}\""
                ">"
            )
            lines.append(f"<operation>{_escape_text(entry['operation'])}</operation>")
            if entry["reason"]:
                lines.append(f"<reason>{_escape_text(entry['reason'])}</reason>")
            lines.append("</entry>")
        lines.append("</recent_entries>")
        lines.append("</operation_log>")
        return "\n".join(lines)


class ThinkingTool:
    """No-op scratchpad tool for reflecting on complex tool results."""

    def think(self, thought: str) -> str:
        """Record a thought without changing external state or fetching data."""

        if not str(thought or "").strip():
            return "No thought was recorded because the input was empty."
        return "Thought recorded."


class CaptchaAuthenticationTool:
    """Captcha authentication tools, including nScreen remote control lifecycle."""

    def __init__(
        self,
        logger: WorkspaceEventLogger | None = None,
        notifier: Callable[[str], None] | None = None,
        service_module: Any | None = None,
        auth_url: str = SHADOW_AUTH_URL,
    ) -> None:
        self.logger = logger or WorkspaceEventLogger()
        self.notifier = notifier
        self.service_module = service_module
        self.auth_url = auth_url

    def authenticate_captcha(
        self,
        current_path: str,
        reason: str = "",
        evidence: str = "",
    ) -> str:
        """Start captcha remote control access and notify the user how to authenticate."""

        normalized_path = str(current_path or "").strip()
        normalized_reason = str(reason or "").strip()
        normalized_evidence = str(evidence or "").strip()
        self.logger.log(
            "captcha_authentication",
            "Captcha authentication was handed off to a human operator.",
            details={
                "current_path": normalized_path,
                "reason": normalized_reason,
                "evidence": normalized_evidence,
            },
        )
        return self.start_authentication_session()

    def captcha_authenticated(
        self,
        current_path: str = "",
        evidence: str = "",
    ) -> str:
        """Stop captcha remote control access after the user reports completion."""

        normalized_path = str(current_path or "").strip()
        normalized_evidence = str(evidence or "").strip()
        self.logger.log(
            "captcha_authenticated",
            "Human operator reported captcha authentication completed.",
            details={
                "current_path": normalized_path,
                "evidence": normalized_evidence,
            },
        )
        return self.stop_authentication_session()

    def start_authentication_session(self) -> str:
        """Start nScreen remote control access and notify the user how to authenticate."""

        service = self._service()
        try:
            result = service.start_shadow_service()
        except Exception as exc:  # noqa: BLE001 - tools return structured text instead of raising.
            return self._error_result("start_shadow_service", exc)

        self.logger.log("shadow_service_start", "Started captcha remote control service.", {"result": result})
        self._notify_user_for_authentication()
        return _xml_block(
            "shadow_service",
            {
                "operation": "start",
                "auth_url": self.auth_url,
            },
            result,
        )

    def stop_authentication_session(self) -> str:
        """Stop nScreen remote control access after captcha authentication."""

        service = self._service()
        try:
            result = service.stop_shadow_service()
        except Exception as exc:  # noqa: BLE001 - tools return structured text instead of raising.
            return self._error_result("stop_shadow_service", exc)

        self.logger.log("shadow_service_stop", "Stopped captcha remote control service.", {"result": result})
        return _xml_block("shadow_service", {"operation": "stop"}, result)

    def _service(self) -> Any:
        if self.service_module is not None:
            return self.service_module
        from nScreen import shadow_root  # noqa: PLC0415

        return shadow_root

    def _notify_user_for_authentication(self) -> None:
        if self.notifier is None:
            return
        message = (
            "验证码认证需要人工协助。\n"
            f"请打开 {self.auth_url} 操作移动设备完成认证。\n"
            "完成后请回复 `done`，我会关闭远程控制端口并继续流程。"
        )
        try:
            self.notifier(message)
        except Exception as exc:  # noqa: BLE001 - notification failure should not interrupt the tool.
            self.logger.log(
                "shadow_service_notice_error",
                "Failed to notify user about captcha remote control.",
                {"error": str(exc), "auth_url": self.auth_url},
            )

    def _error_result(self, operation: str, exc: Exception) -> str:
        self.logger.log(
            "shadow_service_error",
            f"{operation} failed.",
            {"error": f"{type(exc).__name__}: {exc}", "operation": operation},
        )
        return _xml_block(
            "shadow_service_error",
            {"operation": operation},
            {"error": f"{type(exc).__name__}: {exc}"},
        )


class ManualTool:
    """Read page operation manuals from the page_mechanism directory."""

    def __init__(self, page_mechanism_dir: str | Path = PAGE_MECHANISM_DIR) -> None:
        self.page_mechanism_dir = Path(page_mechanism_dir).resolve()

    def query_manual(self, current_path: str) -> str:
        """Return PAGE.md content for the current app or operation path."""

        raw_path = str(current_path or "")
        parts, error = _normalize_manual_path(raw_path)
        if error:
            return self._manual_error(raw_path, "", error)
        if not parts:
            return self._manual_error(raw_path, "", "No manual path was provided.")

        doc, canonical_path = self._manual_doc_for_path(parts)
        if doc is None:
            return self._manual_error(raw_path, canonical_path, "No manual found for the canonical path.")

        lines = [f"<manual_context canonical_path=\"{_escape_attr(canonical_path)}\">"]
        relative_path = doc.relative_to(self.page_mechanism_dir)
        lines.append(f"<page path=\"{relative_path.as_posix()}\">")
        lines.append(doc.read_text(encoding="utf-8"))
        lines.append("</page>")
        lines.append("</manual_context>")
        return "\n".join(lines)

    def _manual_doc_for_path(self, parts: list[str]) -> tuple[Path | None, str]:
        app_slug = _app_slug(parts[0])
        path_parts = [app_slug, *parts[1:]]
        canonical_path = "/".join(path_parts)
        candidate = self.page_mechanism_dir.joinpath(*path_parts, "PAGE.md").resolve()
        if not _is_relative_to(candidate, self.page_mechanism_dir):
            return None, canonical_path
        if candidate.exists():
            return candidate, canonical_path
        return None, canonical_path

    def _manual_error(self, raw_path: str, attempted_path: str, message: str) -> str:
        available_paths = self._available_manual_paths()
        child_paths = self._child_manual_paths(attempted_path)
        lines = [
            "<manual_error>",
            f"<message>{_escape_text(message)}</message>",
            f"<input_path>{_escape_text(raw_path)}</input_path>",
            f"<attempted_path>{_escape_text(attempted_path)}</attempted_path>",
            "<available_paths>",
        ]
        for path in available_paths:
            lines.append(f"<path>{_escape_text(path)}</path>")
        lines.append("</available_paths>")
        if child_paths:
            lines.append("<available_child_paths>")
            for path in child_paths:
                lines.append(f"<path>{_escape_text(path)}</path>")
            lines.append("</available_child_paths>")
        lines.append("</manual_error>")
        return "\n".join(lines)

    def _available_manual_paths(self) -> list[str]:
        paths: list[str] = []
        for doc in self.page_mechanism_dir.rglob("PAGE.md"):
            if not doc.is_file():
                continue
            try:
                relative_parent = doc.parent.relative_to(self.page_mechanism_dir)
            except ValueError:
                continue
            paths.append(relative_parent.as_posix())
        return sorted(paths)

    def _child_manual_paths(self, attempted_path: str) -> list[str]:
        if not attempted_path:
            return []
        parent_path = attempted_path.rsplit("/", 1)[0] if "/" in attempted_path else attempted_path
        parent_dir = self.page_mechanism_dir.joinpath(*parent_path.split("/")).resolve()
        if not _is_relative_to(parent_dir, self.page_mechanism_dir) or not parent_dir.exists():
            return []

        child_paths: list[str] = []
        for doc in parent_dir.glob("*/PAGE.md"):
            if not doc.is_file():
                continue
            try:
                relative_parent = doc.parent.relative_to(self.page_mechanism_dir)
            except ValueError:
                continue
            child_paths.append(relative_parent.as_posix())
        return sorted(child_paths)


def create_common_tools(
    operation_notice_tool: OperationNoticeTool | None = None,
    captcha_authentication_tool: CaptchaAuthenticationTool | None = None,
    thinking_tool: ThinkingTool | None = None,
    manual_tool: ManualTool | None = None,
) -> list[StructuredTool]:
    """Create shared reasoning tools for the LangChain agent."""

    operation_notice = operation_notice_tool or OperationNoticeTool()
    captcha_authentication = captcha_authentication_tool or CaptchaAuthenticationTool()
    thinking = thinking_tool or ThinkingTool()
    manual = manual_tool or ManualTool()
    return [
        StructuredTool.from_function(
            func=operation_notice.notify_user,
            name="notify_user",
            description=(
                "Notify the user before any external device or Excel operation and record "
                "the operation in the live operation log. Inputs: operation, current_path, "
                "optional reason, optional next_page, optional next_path. If the operation "
                "will enter a lower-level page, include next_page and next_path. The returned "
                "operation_log is context for later decisions."
            ),
        ),
        StructuredTool.from_function(
            func=captcha_authentication.authenticate_captcha,
            name="authenticate_captcha",
            description=(
                "Use this tool only when the current UI shows a captcha, security check, "
                "slider verification, or human verification. Inputs: current_path, optional "
                "reason, optional evidence. This tool starts a human captcha authentication "
                f"handoff through {SHADOW_AUTH_URL} and notifies the user how to complete it. "
                "After calling it, stop the current turn and wait for the user to report completion."
            ),
        ),
        StructuredTool.from_function(
            func=captcha_authentication.captcha_authenticated,
            name="captcha_authenticated",
            description=(
                "Call this tool only after the user reports the captcha has been completed. "
                "Inputs: optional current_path and optional evidence. This tool closes the "
                "captcha remote control service; after it returns, re-read the UI with "
                "uiautomate or screenshot before continuing."
            ),
        ),
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
                "Read app/page operation guidance. Input current_path must be a manual canonical "
                "path, not a UI breadcrumb, display title, or merchant name. Use an application "
                "path such as 'com.sankuai.meituan' or 'meituan' for the home manual; do not use "
                "'美团/首页'. Use typed page paths such as 'meituan/外卖' or "
                "'meituan/外卖/商家' for lower-level pages; do not use a concrete merchant name "
                "such as 'meituan/外卖/老乡鸡'. The tool returns only the PAGE.md manual for "
                "the current page path, or manual_error with available canonical paths."
            ),
        ),
    ]


def build_tools() -> list[StructuredTool]:
    """Return the default common tools."""

    return create_common_tools()


def _normalize_manual_path(current_path: str) -> tuple[list[str], str | None]:
    raw_path = str(current_path or "").strip().replace("\\", "/")
    if not raw_path:
        return [], None
    if raw_path.startswith("/"):
        return [], "Manual path must be relative, not absolute."
    if any(part == ".." for part in raw_path.split("/")):
        return [], "Manual path must not contain '..'."

    normalized = posixpath.normpath(raw_path).strip("/")
    if normalized in {"", "."}:
        return [], None

    parts = [part for part in normalized.split("/") if part and part != "."]
    if normalized.startswith("../") or normalized == "..":
        return [], "Manual path must not contain '..'."
    return parts, None


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


def _escape_text(value: Any) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _escape_attr(value: Any) -> str:
    return _escape_text(value).replace('"', "&quot;")


def _xml_block(name: str, attrs: dict[str, Any], payload: Any) -> str:
    attr_text = "".join(f' {key}="{_escape_attr(value)}"' for key, value in attrs.items())
    return (
        f"<{name}{attr_text}>"
        f"{_escape_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))}"
        f"</{name}>"
    )
