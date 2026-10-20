"""Message de-duplication for Feishu channel events."""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from src.common.paths import WORKSPACE_ROOT
from src.common.utils.json import JsonValue, load_json

WORKSPACE_DEDUP_PATH: Final[Path] = WORKSPACE_ROOT / "logs" / "feishu_processed_messages.jsonl"


MESSAGE_CACHE_CAPACITY: Final[int] = 1000


class MessageDeduplicator:
    """LRU message-id de-duplicator backed by the workspace JSONL log."""

    def __init__(self) -> None:
        self.capacity: int = MESSAGE_CACHE_CAPACITY
        self.persistence_path: Path = WORKSPACE_DEDUP_PATH
        self._seen: OrderedDict[str, None] = OrderedDict()
        self._load()

    def contains(self, message_id: str) -> bool:
        if message_id not in self._seen:
            return False
        self._seen.move_to_end(message_id)
        return True

    def mark_processed(self, message_id: str, chat_id: str) -> None:
        self._mark_seen(message_id)
        self._persist(message_id, chat_id)

    def _mark_seen(self, message_id: str) -> None:
        self._seen[message_id] = None
        self._seen.move_to_end(message_id)
        while len(self._seen) > self.capacity:
            self._seen.popitem(last=False)

    def _load(self) -> None:
        if not self.persistence_path.exists():
            return
        try:
            with self.persistence_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    record: JsonValue = load_json(line)
                    if not isinstance(record, dict):
                        continue
                    identity: JsonValue = record.get("message_id")
                    if not isinstance(identity, str):
                        continue
                    message_id: str = identity.strip()
                    if message_id:
                        self._mark_seen(message_id)
        except Exception:
            return

    def _persist(self, message_id: str, chat_id: str) -> None:
        record: dict[str, str] = {
            "message_id": message_id,
            "chat_id": chat_id,
            "receive_time": datetime.now(UTC).isoformat(),
        }
        try:
            self.persistence_path.parent.mkdir(parents=True, exist_ok=True)
            with self.persistence_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        except Exception:
            return
