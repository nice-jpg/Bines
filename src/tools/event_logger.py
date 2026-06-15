"""Workspace-backed event logging for collection/runtime anomalies."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


class WorkspaceEventLogger:
    """Append JSONL event records under ``workspace/logs``."""

    def __init__(self, workspace_dir: str | Path | None = None) -> None:
        self.workspace_dir = Path(workspace_dir) if workspace_dir is not None else _default_workspace_dir()
        self.log_path = self.workspace_dir / "logs" / "events.jsonl"

    def log(self, category: str, message: str, details: dict[str, Any] | None = None) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "category": category,
            "message": message,
            "details": details or {},
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            # Logging must never interrupt collection operations.
            return


def _default_workspace_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "workspace"
