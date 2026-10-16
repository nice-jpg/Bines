"""Workspace-backed event logging for collection/runtime anomalies."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

from src.common.paths import WORKSPACE_ROOT
from src.serialization.json import JsonObject, JsonValue


class EventRecord(TypedDict):
    timestamp: str
    category: str
    message: str
    details: JsonObject


class WorkspaceEventLogger:
    """Append JSONL event records under ``workspace/logs``."""

    def __init__(self) -> None:
        self.workspace_dir: Path = WORKSPACE_ROOT
        self.log_path: Path = self.workspace_dir / "logs" / "events.jsonl"

    def log(self, category: str, message: str, details: Mapping[str, JsonValue] | None = None) -> None:
        record: EventRecord = {
            "timestamp": datetime.now(UTC).isoformat(),
            "category": category,
            "message": message,
            "details": dict(details) if details is not None else {},
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            # Logging must never interrupt collection operations.
            return
