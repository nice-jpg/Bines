"""Typed metadata for automatic external-operation notices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


EXTERNAL_OPERATION_POLICY_METADATA_KEY = "bines_external_operation_policy"
OPERATION_NOTICE_SERVICE_METADATA_KEY = "bines_operation_notice_service"


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


def external_operation_policy_from_metadata(metadata: Any) -> ExternalOperationPolicy | None:
    if not isinstance(metadata, Mapping):
        return None
    policy = metadata.get(EXTERNAL_OPERATION_POLICY_METADATA_KEY)
    return policy if isinstance(policy, ExternalOperationPolicy) else None


def operation_notice_service_from_metadata(metadata: Any) -> OperationNoticeService | None:
    if not isinstance(metadata, Mapping):
        return None
    service = metadata.get(OPERATION_NOTICE_SERVICE_METADATA_KEY)
    return service if service is not None else None


__all__ = [
    "EXTERNAL_OPERATION_POLICY_METADATA_KEY",
    "OPERATION_NOTICE_SERVICE_METADATA_KEY",
    "ExternalOperationPolicy",
    "OperationNoticeService",
    "external_operation_policy_from_metadata",
    "operation_notice_service_from_metadata",
]
