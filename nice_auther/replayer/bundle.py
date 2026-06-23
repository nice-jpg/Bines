"""Replay bundle schema and validation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any


SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReplayBundle:
    schema_version: int
    created_at: str
    input_device: str
    screen_width: int
    screen_height: int
    raw_getevent_log: str
    device_info: dict[str, Any]
    operations: list[dict[str, Any]]
    raw_browser_events: list[dict[str, Any]]
    piar_base64: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReplayBundle":
        if not isinstance(data, dict):
            raise ValueError("bundle must be a JSON object")

        required = ["schema_version", "created_at", "input_device", "screen"]
        missing = [name for name in required if name not in data]
        if missing:
            raise ValueError(f"bundle missing required fields: {', '.join(missing)}")

        schema_version = int(data["schema_version"])
        if schema_version != SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version: {schema_version}")

        screen = data["screen"]
        if not isinstance(screen, dict):
            raise ValueError("screen must be an object")
        try:
            screen_width = int(screen["width"])
            screen_height = int(screen["height"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("screen must include integer width and height") from exc
        if screen_width <= 0 or screen_height <= 0:
            raise ValueError("screen width and height must be positive")

        input_device = str(data["input_device"]).strip()
        if not input_device:
            raise ValueError("input_device must not be empty")

        raw_log = str(data.get("raw_getevent_log") or "")
        piar_base64 = str(data.get("piar_base64") or "")
        if not raw_log and not piar_base64:
            raise ValueError("raw_getevent_log or piar_base64 must not be empty")

        operations = data.get("operations") or []
        if not isinstance(operations, list):
            raise ValueError("operations must be a list")

        raw_browser_events = data.get("raw_browser_events") or []
        if not isinstance(raw_browser_events, list):
            raise ValueError("raw_browser_events must be a list")

        device_info = data.get("device_info") or {}
        if not isinstance(device_info, dict):
            raise ValueError("device_info must be an object")

        return cls(
            schema_version=schema_version,
            created_at=str(data["created_at"]),
            input_device=input_device,
            screen_width=screen_width,
            screen_height=screen_height,
            raw_getevent_log=raw_log,
            device_info=device_info,
            operations=operations,
            raw_browser_events=raw_browser_events,
            piar_base64=piar_base64,
        )

    @classmethod
    def from_json(cls, text: str) -> "ReplayBundle":
        return cls.from_dict(json.loads(text))

    @classmethod
    def from_file(cls, path: str | Path) -> "ReplayBundle":
        return cls.from_json(Path(path).read_text(encoding="utf-8"))


def coerce_bundle(bundle: ReplayBundle | dict[str, Any] | str | Path) -> ReplayBundle:
    if isinstance(bundle, ReplayBundle):
        return bundle
    if isinstance(bundle, dict):
        return ReplayBundle.from_dict(bundle)
    return ReplayBundle.from_file(Path(bundle))
