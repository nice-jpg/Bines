"""Device snapshots behind Reco's public region-reading interface."""

from __future__ import annotations

import json
from threading import RLock
from typing import Callable
from xml.etree.ElementTree import ParseError

from langchain_core.tools import StructuredTool
from Reco.model_interface import TOOLS, PageSession
from .page_change_hint import change_hint
if __package__.startswith("src."):
    from src.page_view import freeze_page, restore_page
else:
    from page_view import freeze_page, restore_page

PAGE_TOOL_NAMES = frozenset(spec["name"] for spec in TOOLS)


class PageBridge:
    """Own one device's current tree; keep XML out of model-visible results."""

    def __init__(self, dump_xml: Callable) -> None:
        self._dump_xml = dump_xml
        self._session: PageSession | None = None
        self._xml = ""
        self._lock = RLock()
        self._snapshot_info = {}

    @property
    def xml(self) -> str:
        """Internal source snapshot shared by Reco and script execution."""
        return self._xml

    def reset(self) -> None:
        with self._lock:
            self._session = None
            self._xml = ""
            self._snapshot_info = {}

    def observe(self, result):
        if not isinstance(result, str):
            return result, None
        with self._lock:
            new = PageSession.build_tree(result)
            initial = self._session is None
            if self._session is None:
                self._session = new
            else:
                self._session.sync(new)
            self._xml = result
            self._snapshot_info = freeze_page(self._session, result)
            if initial:
                return self.query("page_view", {})
            return change_hint(self._session), {"page_xml": self._xml, "page_snapshot": self.snapshot()}

    def snapshot(self):
        with self._lock:
            if self._session is None:
                raise ValueError("Read a page before capturing its view")
            return freeze_page(self._session, self._xml,
                               snapshot_id=self._snapshot_info["snapshot_id"],
                               captured_at=self._snapshot_info["captured_at"])

    def load_snapshot(self, snapshot):
        with self._lock:
            self._session = restore_page(snapshot)
            self._xml = snapshot["xml"]
            self._snapshot_info = dict(snapshot)

    def query(self, name, arguments):
        """Query the current snapshot without capturing or changing the device."""
        with self._lock:
            if self._session is None:
                raise ValueError("No page snapshot; call page_view first")
            result = self._session.call(name, arguments)
            return result, {"page_xml": self._xml, "page_snapshot": self.snapshot()}

    def refresh(self):
        try:
            result = self.observe(self._dump_xml())
        except (ValueError, OSError, ParseError) as exc:
            self.reset()
            return {"ok": False, "error_type": "page_capture_failed", "message": str(exc)}, None
        if result[1] is None:
            self.reset()
        return result

    def call(self, name: str, arguments: dict):
        with self._lock:
            if self._session is None or (name == "page_view" and not arguments.get("offset", 0) and arguments.get("key") is None):
                content, artifact = self.refresh()
                if artifact is None:
                    return content, None
            return self.query(name, arguments)

    def create_tools(self) -> list[StructuredTool]:
        def bind(name):
            def invoke(**kwargs):
                content, artifact = self.call(name, kwargs)
                return json.dumps(content, ensure_ascii=False), artifact
            return invoke

        return [
            StructuredTool(
                name=spec["name"], description=spec["description"],
                args_schema=spec["input_schema"], func=bind(spec["name"]),
                response_format="content_and_artifact",
            )
            for spec in TOOLS
        ]


__all__ = ["PageBridge", "PAGE_TOOL_NAMES"]
