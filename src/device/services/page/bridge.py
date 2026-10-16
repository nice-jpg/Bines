"""Device snapshots behind Reco's public region-reading interface."""

from __future__ import annotations

from _thread import RLock
from collections.abc import Callable
from typing import Final
from xml.etree.ElementTree import ParseError

from Reco.model_interface import TOOLS, PageSession
from src.common.event_log import WorkspaceEventLogger
from src.device.commands.executor import DEFAULT_UI_DUMP_PATH, AndroidCommandExecutor
from src.device.services.page.change_hint import change_hint, root_change_ratio
from src.device.services.page.view import freeze_page, restore_page
from src.device.types.page import PageArtifact, PageSnapshot
from src.device.types.results import CommandResult, DeviceError
from src.serialization.json import JsonObject, JsonValue, json_value

type PageOperationResult = tuple[JsonValue, PageArtifact | None]

PAGE_TOOL_NAMES: Final[frozenset[str]] = frozenset(spec["name"] for spec in TOOLS)


class PageBridge:
    """Own one device's current tree; keep XML out of model-visible results."""

    def __init__(
        self, source: AndroidCommandExecutor | Callable[[], str | DeviceError], *, lock: RLock | None = None
    ) -> None:
        self._source: AndroidCommandExecutor | Callable[[], str | DeviceError] = source
        self._logger: WorkspaceEventLogger = WorkspaceEventLogger()
        self._session: PageSession | None = None
        self._xml: str = ""
        self._lock: RLock = RLock() if lock is None else lock
        self._snapshot_info: PageSnapshot | None = None

    def _dump_xml(self) -> str | DeviceError:
        if not isinstance(self._source, AndroidCommandExecutor):
            return self._source()
        for operation in (self._source.disable_animations, self._source.dump_hierarchy):
            result: CommandResult = operation()
            if result.error is not None:
                self._logger.log(result.error.error_type, result.error.message, result.error.details)
                return result.error
        result = self._source.read_file(DEFAULT_UI_DUMP_PATH)
        if result.error is not None:
            self._logger.log(result.error.error_type, result.error.message, result.error.details)
            return result.error
        xml: str = result.stdout.strip()
        for marker in ("<?xml", "<hierarchy"):
            start: int = xml.find(marker)
            if start >= 0:
                xml = xml[start:]
                end: int = xml.find("</hierarchy>")
                if end >= 0:
                    xml = xml[: end + len("</hierarchy>")]
                break
        if (
            any(word in xml for word in ("验证码", "安全验证", "滑块验证", "人机验证"))
            or "captcha" in xml.lower()
        ):
            self._logger.log("captcha", "Detected captcha signal in UI hierarchy", {"source": "page_view"})
        if any(
            word in xml for word in ("优惠券", "红包", "弹窗", "广告", "立即领取", "以后再说", "暂不", "关闭")
        ):
            self._logger.log("popup", "Detected popup signal in UI hierarchy", {"source": "page_view"})
        return xml.strip()

    @property
    def operation_lock(self) -> RLock:
        """Serialize device operations and reads with view synchronization."""
        return self._lock

    @property
    def xml(self) -> str:
        """Internal source snapshot shared by Reco and script execution."""
        return self._xml

    def reset(self) -> None:
        with self._lock:
            self._session = None
            self._xml = ""
            self._snapshot_info = None

    def observe(self, result: str | DeviceError) -> PageOperationResult:
        if isinstance(result, DeviceError):
            return result.as_dict(), None
        with self._lock:
            new: PageSession = PageSession.build_tree(result)
            initial: bool = self._session is None
            if self._session is None:
                self._session = new
            else:
                self._session.sync(new)
            self._xml = result
            self._snapshot_info = freeze_page(self._session, result)
            if initial:
                return self.query("page_view", {})
            return json_value(change_hint(self._session)), {
                "page_xml": self._xml,
                "page_snapshot": self.snapshot(),
            }

    def snapshot(self) -> PageSnapshot:
        with self._lock:
            if self._session is None or self._snapshot_info is None:
                raise ValueError("Read a page before capturing its view")
            return freeze_page(
                self._session,
                self._xml,
                snapshot_id=self._snapshot_info["snapshot_id"],
                captured_at=self._snapshot_info["captured_at"],
            )

    def root_change_ratio(self) -> float | None:
        """Return normalized whole-page region change, not pixel-area change."""
        with self._lock:
            if self._session is None:
                return None
            return root_change_ratio(self._session)

    def load_snapshot(self, snapshot: PageSnapshot) -> None:
        with self._lock:
            self._session = restore_page(snapshot)
            self._xml = snapshot["xml"]
            self._snapshot_info = snapshot.copy()

    def query(self, name: str, arguments: JsonObject) -> PageOperationResult:
        """Query the current snapshot without capturing or changing the device."""
        with self._lock:
            if self._session is None:
                raise ValueError("No page snapshot; call page_view first")
            result: JsonValue = self._session.call(name, arguments)
            return result, {"page_xml": self._xml, "page_snapshot": self.snapshot()}

    def refresh(self) -> PageOperationResult:
        with self._lock:
            return self._refresh()

    def _refresh(self) -> PageOperationResult:
        try:
            result: PageOperationResult = self.observe(self._dump_xml())
        except (ValueError, OSError, ParseError) as exc:
            self.reset()
            return {"ok": False, "error_type": "page_capture_failed", "message": str(exc)}, None
        if result[1] is None:
            self.reset()
        return result

    def call(self, name: str, arguments: JsonObject) -> PageOperationResult:
        with self._lock:
            if self._session is None or (
                name == "page_view" and not arguments.get("offset", 0) and arguments.get("key") is None
            ):
                content, artifact = self.refresh()
                if artifact is None:
                    return content, None
            return self.query(name, arguments)


__all__: list[str] = ["PageBridge", "PAGE_TOOL_NAMES"]
