"""Execution lifecycle, control state and immutable outcomes."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from src.json_types import JsonValue


class EngineStatus(StrEnum):
    COMPLETED = "completed"
    CONTINUE_REQUIRED = "continue_required"
    RESUME_READY = "resume_ready"
    INTERRUPTED = "interrupted"
    STOPPED = "stopped"


class InterruptKind(StrEnum):
    HUMAN = "human"
    LIVENESS = "liveness"
    MODEL_TRANSPORT = "model_transport"
    RECURSION = "recursion"
    EXECUTION = "execution"


@dataclass(frozen=True)
class EngineResult:
    status: EngineStatus
    output: str = ""
    interrupt_kind: InterruptKind | None = None

    def __post_init__(self) -> None:
        if (self.status == EngineStatus.INTERRUPTED) != (self.interrupt_kind is not None):
            raise ValueError("Only interrupted results must carry an interrupt kind")


class EngineLifecycle(StrEnum):
    OPEN = "open"
    STOPPED = "stopped"
    CLOSED = "closed"


@dataclass
class ExecutionState:
    status: EngineStatus | None = None
    interrupt_kind: InterruptKind | None = None
    continuation_reason: str = ""
    resume_input: JsonValue = None


type YieldReason = Literal["shutdown_requested", "user_message_pending"]
