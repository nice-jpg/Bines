"""Typed metadata for automatic external-operation notices."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Protocol, runtime_checkable

from src.serialization.validation import is_string_mapping

EXTERNAL_OPERATION_POLICY_METADATA_KEY: Final[str] = "bines_external_operation_policy"


@runtime_checkable
class OperationNoticeService(Protocol):
    def notify_user(
        self,
        operation: str,
        current_path: str,
        reason: str = "",
        next_page: str = "",
        next_path: str = "",
    ) -> str: ...


@dataclass(frozen=True)
class ExternalOperationPolicy:
    """Declare an operation that must be announced immediately before execution."""

    reason: str
    next_path_argument: str | None = None


def external_operation_policy_from_metadata(metadata: object) -> ExternalOperationPolicy | None:
    if not is_string_mapping(metadata):
        return None
    policy: object = metadata.get(EXTERNAL_OPERATION_POLICY_METADATA_KEY)
    return policy if isinstance(policy, ExternalOperationPolicy) else None


__all__: list[str] = [
    "EXTERNAL_OPERATION_POLICY_METADATA_KEY",
    "ExternalOperationPolicy",
    "OperationNoticeService",
    "external_operation_policy_from_metadata",
]
