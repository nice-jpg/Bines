"""Select a bounded search frontier from Reco's existing sync evidence."""

from collections.abc import Mapping
from dataclasses import dataclass
from math import isclose, sqrt

from Reco._sync import SyncNodeStats
from Reco.model_interface import PageSession
from src.device.types.page import PageChangeHint


@dataclass(frozen=True)
class _Selection:
    score: float = 0
    covered: int = 0
    specificity: int = 0
    ids: tuple[int, ...] = ()

    def combine(self, other: _Selection) -> _Selection:
        return _Selection(
            self.score + other.score,
            self.covered + other.covered,
            self.specificity + other.specificity,
            self.ids + other.ids,
        )


def root_change_ratio(session: PageSession) -> float | None:
    """Fraction of compared regions changed in the latest whole-page sync.

    The comparison population includes inserted/deleted regions and the larger
    side of replacements. Unlike change count divided by the old tree size, it
    cannot saturate at one while unchanged regions remain. This is not pixel area.
    """
    if not session.last_sync_stats:
        return None
    root: SyncNodeStats = next(node for node in session.last_sync_stats.values() if node.parent_id is None)
    return root.changed_count / root.compared_count


def change_hint(session: PageSession) -> PageChangeHint:
    """Return whole-page change ratio and at most three disjoint region entrances."""
    stats: Mapping[int, SyncNodeStats] = session.last_sync_stats
    order: dict[int, int] = {}

    def better(left: _Selection, right: _Selection) -> bool:
        if not isclose(left.score, right.score, rel_tol=1e-12, abs_tol=1e-12):
            return left.score > right.score
        if (left.covered, left.specificity) != (right.covered, right.specificity):
            return (left.covered, left.specificity) > (right.covered, right.specificity)
        return tuple(order[k] for k in left.ids) < tuple(order[k] for k in right.ids)

    def visit(key: int, parent_changed: bool = False, depth: int = 0) -> tuple[dict[int, _Selection], bool]:
        node: SyncNodeStats = stats[key]
        order[key] = len(order)
        choices: dict[int, _Selection] = {0: _Selection()}
        candidate: bool = (node.self_changed and not parent_changed) or node.removed_count > 0
        # New/replaced subtrees are indivisible; stable nodes split local-change
        # components. Ancestors may merge fragments without exposing their text.
        if not node.atomic:
            for child in node.children:
                child_choices, child_candidate = visit(child, node.self_changed, depth + 1)
                candidate |= child_candidate
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
                node.changed_count / sqrt(node.compared_count),
                node.changed_count,
                depth,
                (key,),
            )
            if 1 not in choices or better(selected, choices[1]):
                choices[1] = selected
        return choices, candidate

    selected: _Selection = _Selection()
    page_update_ratio: float = 0.0
    if stats:
        root: int = next(key for key, node in stats.items() if node.parent_id is None)
        root_stats: SyncNodeStats = stats[root]
        page_update_ratio = root_stats.changed_count / root_stats.compared_count
        choices, _ = visit(root)
        for choice in choices.values():
            if better(choice, selected):
                selected = choice
    return {
        "kind": "page_change_hint",
        "update_ratio": page_update_ratio,
        "regions": [{"id": key, "update_ratio": session.update_ratio(key)} for key in selected.ids],
    }
