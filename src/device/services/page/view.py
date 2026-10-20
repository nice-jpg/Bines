"""Frozen public Reco views and portable annotation snapshots."""

from collections.abc import Sequence
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from Reco.model_interface import PageSession
from src.common.utils.json import JsonObject, JsonValue, json_object
from src.device.types.page import PageSnapshot


class MissingPageAlias(ValueError):
    """A required semantic region is no longer available."""


def region_paths(session: PageSession) -> dict[int, list[int]]:
    """Map public IDs to child-index paths independent of sync-generated IDs."""
    paths: dict[int, list[int]] = {}

    def visit(key: int | None, path: list[int]) -> None:
        view: JsonObject = json_object(session.call("page_view", {} if key is None else {"key": key}))
        identity: JsonValue = view["id"]
        if not isinstance(identity, int):
            raise ValueError("Page region has no integer ID")
        paths[identity] = path
        regions: JsonValue = view.get("regions", [])
        if not isinstance(regions, list):
            raise ValueError("Page regions must be a list")
        for index, child in enumerate(regions):
            if not isinstance(child, dict):
                raise ValueError("Page region must be an object")
            child_id: JsonValue = child.get("id")
            if not isinstance(child_id, int):
                raise ValueError("Page region has no integer ID")
            visit(child_id, [*path, index])

    visit(None, [])
    return paths


def freeze_page(
    session: PageSession, xml: str, *, snapshot_id: str | None = None, captured_at: str | None = None
) -> PageSnapshot:
    aliases: dict[str, list[int]] = {}
    for key, path in region_paths(session).items():
        node: JsonObject = json_object(session.call("page_node", {"key": key}))
        alias: JsonValue = node.get("aaid")
        if isinstance(alias, str) and alias:
            aliases[alias] = path
    return {
        "xml": xml,
        "aliases": aliases,
        "snapshot_id": snapshot_id or uuid4().hex,
        "captured_at": captured_at or datetime.now(UTC).isoformat(),
    }


def restore_page(snapshot: PageSnapshot) -> PageSession:
    session: PageSession = PageSession.build_tree(snapshot["xml"])
    ids: dict[tuple[int, ...], int] = {tuple(path): key for key, path in region_paths(session).items()}
    for alias, path in snapshot["aliases"].items():
        if tuple(path) not in ids:
            raise MissingPageAlias(f"Invalid saved region for alias {alias}")
        session.call("update_aaid", {"id": ids[tuple(path)], "value": alias})
    return session


class ReadOnlyPageView:
    """Exact public region queries over an independent, immutable page snapshot."""

    __slots__: tuple[str] = ("__session",)

    def __init__(self, snapshot: PageSnapshot) -> None:
        self.__session: PageSession = restore_page(deepcopy(snapshot))

    def page_view(self, key: int | None = None, offset: int = 0, limit: int = -1) -> JsonObject:
        args: dict[str, int] = {"offset": offset, "limit": limit}
        if key is not None:
            args["key"] = key
        return json_object(self.__session.call("page_view", args))

    def page_catalog(self, offset: int = 0, limit: int = -1) -> JsonObject:
        return json_object(self.__session.call("page_catalog", {"offset": offset, "limit": limit}))

    def page_read(
        self,
        key: int | None = None,
        offset: int = 0,
        limit: int = -1,
        char_offset: int = 0,
        max_chars: int = -1,
    ) -> JsonObject:
        args: dict[str, int] = {
            "offset": offset,
            "limit": limit,
            "char_offset": char_offset,
            "max_chars": max_chars,
        }
        if key is not None:
            args["key"] = key
        return json_object(self.__session.call("page_read", args))

    def page_node(self, key: int) -> JsonObject:
        return json_object(self.__session.call("page_node", {"key": key}))

    def select_aaid(self, aaid: int) -> JsonObject:
        try:
            node: JsonObject = json_object(self.__session.call("select_aaid", {"aaid": aaid}))
        except ValueError as exc:
            raise MissingPageAlias(f"Required alias {aaid} is missing") from exc
        if node.get("node", True) is None:
            raise MissingPageAlias(f"Required alias {aaid} was deleted")
        return json_object(node)

    def require_aliases(self, aliases: Sequence[str]) -> None:
        for alias in aliases:
            self.select_aaid(int(alias))
