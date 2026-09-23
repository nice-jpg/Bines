"""Frozen public Reco views and portable annotation snapshots."""

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

from Reco.model_interface import PageSession


class MissingPageAlias(ValueError):
    """A required semantic region is no longer available."""


def region_paths(session):
    """Map public IDs to child-index paths independent of sync-generated IDs."""
    paths = {}

    def visit(key, path):
        view = session.call("page_view", {} if key is None else {"key": key})
        paths[view["id"]] = path
        for index, child in enumerate(view.get("regions", [])):
            visit(child["id"], [*path, index])

    visit(None, [])
    return paths


def freeze_page(session, xml, *, snapshot_id=None, captured_at=None):
    aliases = {}
    for key, path in region_paths(session).items():
        node = session.call("page_node", {"key": key})
        if node.get("aaid"):
            aliases[node["aaid"]] = path
    return {
        "xml": xml, "aliases": aliases,
        "snapshot_id": snapshot_id or uuid4().hex,
        "captured_at": captured_at or datetime.now(timezone.utc).isoformat(),
    }


def restore_page(snapshot):
    session = PageSession.build_tree(snapshot["xml"])
    ids = {tuple(path): key for key, path in region_paths(session).items()}
    for alias, path in snapshot["aliases"].items():
        if tuple(path) not in ids:
            raise MissingPageAlias(f"Invalid saved region for alias {alias}")
        session.call("update_aaid", {"id": ids[tuple(path)], "value": alias})
    return session


class ReadOnlyPageView:
    """Exact public region queries over an independent, immutable page snapshot."""

    __slots__ = ("__session",)

    def __init__(self, snapshot):
        self.__session = restore_page(deepcopy(snapshot))

    def page_view(self, key=None, offset=0, limit=-1):
        args = {"offset": offset, "limit": limit}
        if key is not None:
            args["key"] = key
        return deepcopy(self.__session.call("page_view", args))

    def page_catalog(self, offset=0, limit=-1):
        return deepcopy(self.__session.call("page_catalog", {"offset": offset, "limit": limit}))

    def page_read(self, key=None, offset=0, limit=-1, char_offset=0, max_chars=-1):
        args = {"offset": offset, "limit": limit, "char_offset": char_offset, "max_chars": max_chars}
        if key is not None:
            args["key"] = key
        return deepcopy(self.__session.call("page_read", args))

    def page_node(self, key):
        return deepcopy(self.__session.call("page_node", {"key": key}))

    def select_aaid(self, aaid):
        try:
            node = self.__session.call("select_aaid", {"aaid": aaid})
        except ValueError as exc:
            raise MissingPageAlias(f"Required alias {aaid} is missing") from exc
        if node.get("node", True) is None:
            raise MissingPageAlias(f"Required alias {aaid} was deleted")
        return deepcopy(node)

    def require_aliases(self, aliases):
        for alias in aliases:
            self.select_aaid(int(alias))
