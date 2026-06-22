"""Message de-duplication for Feishu channel events."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

WORKSPACE_DEDUP_PATH = Path(__file__).resolve().parents[3] / "workspace" / "logs" / "feishu_processed_messages.jsonl"


class MessageDeduplicator:
    """LRU message-id de-duplicator with optional JSONL persistence."""

    def __init__(
        self,
        *,
        capacity: int = 1000,
        persistence_path: str | Path | None = WORKSPACE_DEDUP_PATH,
    ) -> None:
        self.capacity = max(1, int(capacity))
        self.persistence_path = Path(persistence_path) if persistence_path is not None else None
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._load()

    def should_process(self, message_id: str, *, chat_id: str = "") -> bool:
        normalized = str(message_id or "").strip()
        if not normalized:
            return True
        if normalized in self._seen:
            self._seen.move_to_end(normalized)
            return False
        self._mark_seen(normalized)
        self._persist(normalized, chat_id)
        return True

    def _mark_seen(self, message_id: str) -> None:
        self._seen[message_id] = None
        self._seen.move_to_end(message_id)
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)

    def _load(self) -> None:
        if self.persistence_path is None or not self.persistence_path.exists():
            return
        try:
            with self.persistence_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    record = json.loads(line)
                    message_id = str(record.get("message_id") or "").strip()
                    if message_id:
                        self._mark_seen(message_id)
        except Exception:
            return

    def _persist(self, message_id: str, chat_id: str) -> None:
        if self.persistence_path is None:
            return
        record: dict[str, Any] = {
            "message_id": message_id,
            "chat_id": chat_id,
            "receive_time": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            with self.persistence_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            return
