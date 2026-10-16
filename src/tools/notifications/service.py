"""Automatic lifecycle notifications, not a model-callable tool."""

from collections import deque
from collections.abc import Callable
from typing import Final

from src.common.event_log import WorkspaceEventLogger
from src.tools.shared.xml import escape_attr as _escape_attr
from src.tools.shared.xml import escape_text as _escape_text

OPERATION_LOG_CAPACITY: Final[int] = 30


class OperationNoticeTool:
    """Record user-visible operation notices before external actions."""

    def __init__(
        self,
        notifier: Callable[[str], None] | None = None,
    ) -> None:
        self.logger: WorkspaceEventLogger = WorkspaceEventLogger()
        self.max_entries: int = OPERATION_LOG_CAPACITY
        self.notifier: Callable[[str], None] | None = notifier
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

        normalized_operation: str = str(operation or "").strip()
        if not normalized_operation:
            return "No operation notice was recorded because the operation input was empty."

        entry: dict[str, str] = {
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
        lines: list[str] = [f"Operation: {entry['operation']}"]
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
        lines: list[str] = ["<operation_log>"]
        lines.append(f'<recent_entries count="{len(self._entries)}" max="{self.max_entries}">')
        for index, entry in enumerate(self._entries, start=1):
            lines.append(
                "<entry "
                f'index="{index}" '
                f'current_path="{_escape_attr(entry["current_path"])}" '
                f'next_page="{_escape_attr(entry["next_page"])}" '
                f'next_path="{_escape_attr(entry["next_path"])}"'
                ">"
            )
            lines.append(f"<operation>{_escape_text(entry['operation'])}</operation>")
            if entry["reason"]:
                lines.append(f"<reason>{_escape_text(entry['reason'])}</reason>")
            lines.append("</entry>")
        lines.append("</recent_entries>")
        lines.append("</operation_log>")
        return "\n".join(lines)
