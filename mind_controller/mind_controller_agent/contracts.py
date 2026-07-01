"""Public contracts between the cognitive master and a controlled slave."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class SlaveDebugInfo:
    """Information the master needs to reason about and edit slave prompts."""

    prompt_paths: tuple[Path, ...]
    prompt_structure: str
    responsibility: str
    expected_outcome: str
    additional_context: Mapping[str, Any] = field(default_factory=dict)
    working_directory: Path | None = None


@runtime_checkable
class CognitiveSlave(Protocol):
    """The two required slave interfaces consumed by the master."""

    def run(self) -> Any:
        """Return the full result with a ``summary`` view for the master LLM."""

    def eval(self, result: Any) -> int | Mapping[str, Any]:
        """Evaluate exactly the result returned by ``run``."""


@runtime_checkable
class SlaveDebugInfoProvider(Protocol):
    """Optional convenience interface for supplying separate debug metadata."""

    def debug_info(self) -> SlaveDebugInfo:
        """Describe editable prompts, role, and desired behavior."""
