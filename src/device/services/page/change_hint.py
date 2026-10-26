"""Select a bounded search frontier from Reco's visible-area changes."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isclose

from Reco._sync import SyncNodeStats
from Reco.model_interface import PageSession
from src.device.types.page import PageChangeHint


@dataclass(frozen=True)
class _Selection:
    score: float = 0
    specificity: int = 0
    ids: tuple[int, ...] = ()

    def combine(self, other: _Selection) -> _Selection:
        return _Selection(
            self.score + other.score,
            self.specificity + other.specificity,
            self.ids + other.ids,
        )


def root_change_ratio(session: PageSession) -> float | None:
    """Latest whole-page visible-area change relative to the old visible area."""
    if not session.last_sync_stats:
        return None
    root: int = next(key for key, node in session.last_sync_stats.items() if node.parent_id is None)
    return session.view_update_ratio(root)


def change_hint(session: PageSession) -> PageChangeHint:
    """Return whole-page change ratio and at most three disjoint region entrances."""
    stats: Mapping[int, SyncNodeStats] = session.last_sync_stats
    order: dict[int, int] = {}

    def better(left: _Selection, right: _Selection) -> bool:
        if not isclose(left.score, right.score, rel_tol=1e-12, abs_tol=1e-12):
            return left.score > right.score
        if left.specificity != right.specificity:
            return left.specificity > right.specificity
        return tuple(order[k] for k in left.ids) < tuple(order[k] for k in right.ids)

    def visit(key: int, depth: int = 0) -> dict[int, _Selection]:
        node: SyncNodeStats = stats[key]
        order[key] = len(order)
        choices: dict[int, _Selection] = {0: _Selection()}
        ratio: float = session.view_update_ratio(key)
        candidate: bool = ratio > 0
        # Search only by visible-area evidence; zero-area new IDs are not
        # candidates. Surviving ancestors anchor deletion/replacement changes.
        if node.children:
            for child in node.children:
                child_choices = visit(child, depth + 1)
                merged: dict[int, _Selection] = {}
                for count, selection in choices.items():
                    for child_count, child_selection in child_choices.items():
                        total: int = count + child_count
                        if total > 3:
                            continue
                        combined: _Selection = selection.combine(child_selection)
                        if total not in merged or better(combined, merged[total]):
                            merged[total] = combined
                choices = merged
        if candidate:
            selected: _Selection = _Selection(
                ratio,
                depth,
                (key,),
            )
            if 1 not in choices or better(selected, choices[1]):
                choices[1] = selected
        return choices

    selected: _Selection = _Selection()
    page_node_ratio: float = 0.0
    page_view_ratio: float = 0.0
    if stats:
        root: int = next(key for key, node in stats.items() if node.parent_id is None)
        page_node_ratio = session.node_update_ratio(root)
        page_view_ratio = session.view_update_ratio(root)
        if page_view_ratio > 0:
            choices = visit(root)
            for choice in choices.values():
                if better(choice, selected):
                    selected = choice
    return {
        "kind": "page_change_hint",
        "node_update_ratio": page_node_ratio,
        "view_update_ratio": page_view_ratio,
        "regions": [
            {"id": key, "node_update_ratio": session.node_update_ratio(key),
             "view_update_ratio": session.view_update_ratio(key)} for key in selected.ids
        ],
    }
