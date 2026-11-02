"""Per-agent task records. The mailbox router serializes all mutations."""

from dataclasses import dataclass, replace
from typing import Literal

from src.common.utils.json import JsonObject

from .contracts import TaskResult


@dataclass(frozen=True)
class TaskRecord:
    task_id: str
    correlation_id: str
    peer_id: str
    instruction: str
    origin: JsonObject
    status: Literal["submitted", "running", "progress", "blocked", "awaiting_review"] = "submitted"
    result: TaskResult | None = None


class TaskManager:
    """Keep received work and delegated work separate, even when topics coincide."""

    def __init__(self) -> None:
        self.received: dict[str, TaskRecord] = {}
        self.sent: dict[str, TaskRecord] = {}
        self.has_reported = False

    def receive(self, record: TaskRecord) -> None:
        existing = self.received.get(record.task_id)
        if existing is not None and existing.correlation_id != record.correlation_id:
            raise ValueError("Task topic is already assigned")
        self.received[record.task_id] = replace(record, status="running")

    def report(self, task_id: str, result: TaskResult) -> None:
        self.has_reported = True
        if result.status in {"completed", "failed", "cancelled"}:
            del self.received[task_id]
        else:
            self.received[task_id] = replace(
                self.received[task_id], status=result.status, result=result.model_copy(deep=True)
            )

    def receive_report(self, task_id: str, result: TaskResult) -> None:
        record = self.sent[task_id]
        status = "awaiting_review" if result.status in {"completed", "failed", "cancelled"} else result.status
        self.sent[task_id] = replace(record, status=status, result=result.model_copy(deep=True))

    def accept(self, task_id: str) -> TaskRecord:
        record = self.sent[task_id]
        if record.status != "awaiting_review":
            raise ValueError("A received final report is required before acceptance")
        return self.sent.pop(task_id)


def validate_task_topic(task_id: str) -> str:
    topic = task_id.strip()
    if not topic or len(topic) > 64 or "\n" in topic or "\r" in topic:
        raise ValueError("task_id must be a short, single-line task topic (1-64 characters)")
    return topic
