"""Shared result helpers for device and tool operations."""

from __future__ import annotations

from typing import Any

ErrorResult = dict[str, Any]


def make_error_result(
    error_type: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> ErrorResult:
    return {
        "ok": False,
        "error_type": error_type,
        "message": message,
        "details": details or {},
    }


def is_error_result(value: object) -> bool:
    return isinstance(value, dict) and value.get("ok") is False
