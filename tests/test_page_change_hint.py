"""Bounded search hints and Reco sync statistics, without device dependencies."""

from dataclasses import FrozenInstanceError
import json
from unittest.mock import patch

import pytest
from Reco.model_interface import PageSession
from src.tools.page_bridge import PageBridge
from src.tools.page_change_hint import change_hint


def button(text, kind="Button"):
    return f'<node class="android.widget.{kind}" text="{text}"/>'


def container(children, kind="ScrollView", text=""):
    return f'<node class="android.widget.{kind}" text="{text}">{children}</node>'


def page(*regions):
    return "<hierarchy>" + "".join(regions) + "</hierarchy>"


def items(count, prefix="old"):
    return "".join(button(f"{prefix}-{i}") for i in range(count))


def synced(before, after):
    session = PageSession(before)
    session.sync(PageSession(after))
    return session


def ids(session):
    return [r["id"] for r in change_hint(session)["regions"]]


def assert_frontier(session):
    hint = change_hint(session)
    assert set(hint) == {"kind", "regions"}
    selected = ids(session)
    assert len(selected) <= 3
    assert len(selected) == len(set(selected))
    for region in hint["regions"]:
        assert set(region) == {"id", "update_ratio"}
        assert session.call("page_node", {"key": region["id"]})["id"] == region["id"]
        assert region["update_ratio"] == session.update_ratio(region["id"])
        parent = session.last_sync_stats[region["id"]].parent_id
        while parent is not None:
            assert parent not in selected
            parent = session.last_sync_stats[parent].parent_id
    assert change_hint(session) == hint


def test_local_change_does_not_mark_ancestors_and_stats_are_readonly():
    session = synced(page(container(button("old"))), page(container(button("new"))))
    root, group, leaf = (session.last_sync_stats[i] for i in range(3))
    assert not root.self_changed and not group.self_changed and leaf.self_changed
    assert (root.changed_count, root.compared_count) == (1, 3)
    assert (group.changed_count, group.compared_count) == (1, 2)
    assert ids(session) == [2]
    with pytest.raises(FrozenInstanceError):
        leaf.changed_count = 10
    with pytest.raises(TypeError):
        session.last_sync_stats[2] = root
    session.sync(PageSession(page(container(button("new")))))
    assert ids(session) == []
    assert all(node.changed_count == 0 for node in session.last_sync_stats.values())


def test_stable_container_merges_many_disconnected_changed_leaves():
    session = synced(
        page(button("header"), container(items(20)), button("footer")),
        page(button("header"), container(items(20, "new")), button("footer")),
    )
    assert ids(session) == [2]
    group = session.last_sync_stats[2]
    assert not group.self_changed
    assert (group.changed_count, group.compared_count) == (20, 21)
    assert_frontier(session)
    # The hint is not a filter: untouched regions and all list items remain readable.
    assert session.call("page_read", {"key": 1})["entries"][0]["value"] == "header"
    assert len(session.call("page_view", {"key": 2, "limit": -1})["regions"]) == 20


def test_connected_local_changes_stay_one_component():
    session = synced(
        page(container(button("old"), text="old-group")),
        page(container(button("new"), text="new-group")),
    )
    assert ids(session) == [1]
    assert session.last_sync_stats[1].self_changed
    assert session.last_sync_stats[2].self_changed


@pytest.mark.parametrize("extra", ["added", "removed"])
def test_high_ratio_leaf_does_not_displace_more_valuable_lists(extra):
    before_groups = [container(items(30, f"group-{i}")) for i in range(3)]
    after_groups = [container(items(12, f"new-{i}") + "".join(
        button(f"group-{i}-{j}") for j in range(12, 30)
    )) for i in range(3)]
    leaf_group = container(button("fixed") + (button("leaf") if extra == "removed" else ""))
    new_leaf_group = container(button("fixed") + (button("leaf") if extra == "added" else ""))
    session = synced(page(*before_groups, leaf_group), page(*after_groups, new_leaf_group))
    assert ids(session) == [1, 32, 63]
    assert_frontier(session)


@pytest.mark.parametrize("kind", ["added", "replaced"])
def test_new_and_replaced_subtrees_are_atomic_search_regions(kind):
    before = page(button("fixed")) if kind == "added" else page(button("fixed"), button("old"))
    session = synced(before, page(button("fixed"), container(items(20))))
    selected = ids(session)
    assert len(selected) == 1
    node = session.last_sync_stats[selected[0]]
    assert node.atomic and len(node.children) == 20
    assert node.changed_count == node.compared_count == 21
    assert_frontier(session)


def test_removal_counts_old_nodes_and_never_returns_a_deleted_id():
    session = synced(page(container(items(20))), page(container(button("old-0"))))
    assert ids(session) == [1]
    group = session.last_sync_stats[1]
    assert not group.self_changed
    assert (group.changed_count, group.compared_count, group.removed_count) == (19, 21, 19)
    assert session.update_ratio(1) == 19 / 21
    assert_frontier(session)
    assert not set(ids(session)) & session._deleted_ids.keys()


def test_replacement_uses_larger_old_subtree_for_comparison_size():
    session = synced(page(container(items(20))), page(button("replacement")))
    key, = ids(session)
    node = session.last_sync_stats[key]
    assert node.atomic and not node.children
    assert node.changed_count == node.compared_count == 21
    assert session.last_sync_stats[0].compared_count == 22
    assert_frontier(session)


def test_ties_prefer_coverage_before_specificity():
    # 3 / sqrt(9) == 1: a group covering three changes beats a single leaf.
    session = synced(
        page(container(items(8)), button("a"), button("b"), button("c")),
        page(container(items(3, "new") + "".join(button(f"old-{i}") for i in range(3, 8))),
             button("new-a"), button("new-b"), button("new-c")),
    )
    # Equal-value outside leaves are selected in tree order, not ID allocation order.
    assert ids(session) == [1, 10, 11]
    assert_frontier(session)


def test_equal_value_and_coverage_prefers_deeper_region_then_tree_order():
    session = synced(
        page(container(items(8)), button("outside")),
        page(container(items(3, "new") + "".join(button(f"old-{i}") for i in range(3, 8))),
             button("new-outside")),
    )
    # Four changed leaves have equal value. Retain the three more specific ones.
    assert ids(session) == [2, 3, 4]
    assert_frontier(session)


def test_final_tie_uses_tree_order_not_allocated_ids():
    session = synced(
        page(*(button(f"old-{i}") for i in range(4))),
        page(button("replacement", "ImageView"), *(button(f"new-{i}") for i in range(1, 4))),
    )
    assert ids(session) == [5, 2, 3]
    assert_frontier(session)


def test_removed_only_child_anchors_at_surviving_container():
    session = synced(page(container(items(4))), page(container("")))
    assert ids(session) == [1]
    assert session.last_sync_stats[1].changed_count == 4
    assert session.last_sync_stats[1].compared_count == 5
    assert_frontier(session)


def test_failed_sync_keeps_previous_statistics():
    session = synced(page(button("old")), page(button("new")))
    previous = session.last_sync_stats
    with pytest.raises(ValueError):
        session.sync(session)
    assert session.last_sync_stats is previous
    assert ids(session) == [1]


def test_whole_page_change_stays_bounded_and_initial_capture_is_full():
    before = page(container(items(60)), button("old-footer"))
    after = page(button("new-header"), container(items(80, "new")))
    bridge = PageBridge(lambda: before)
    initial, _ = bridge.refresh()
    assert "regions" in initial and "kind" not in initial
    hint, artifact = bridge.observe(after)
    assert hint["kind"] == "page_change_hint" and len(hint["regions"]) <= 3
    assert artifact["page_xml"] == after
    assert artifact["page_snapshot"]["xml"] == after
    for region in hint["regions"]:
        assert bridge.query("page_node", {"key": region["id"]})[0]["id"] == region["id"]
    # Selection does not invoke public queries to serialize summaries or descendants.
    with patch.object(bridge._session, "call", side_effect=AssertionError("unexpected query")):
        assert change_hint(bridge._session) == hint


def legacy_delta(session):
    """Reproduce the former payload for size comparisons only."""
    stats = session.last_sync_stats

    def node(key, recursive=False):
        children = stats[key].children
        return {**session.call("page_node", {"key": key}),
                "parent_id": stats[key].parent_id,
                "children": [node(c, True) for c in children] if recursive else list(children)}

    return {"kind": "page_delta", "changes": [
        dict(c, node=node(c["id"], c["kind"] in {"added", "replaced"}))
        if c["kind"] != "removed" else dict(c) for c in session.last_changes
    ]}


def test_hint_size_is_bounded_as_changed_content_grows():
    measurements = []
    for count in (30, 300):
        session = synced(page(container(items(count))), page(container(items(count, "new"))))
        length = lambda value: len(json.dumps(value, ensure_ascii=False))
        hint_size = length(change_hint(session))
        old_size = length(legacy_delta(session))
        root_size = length(session.call("page_view", {}))
        query_size = length(session.call("page_view", {"key": ids(session)[0]}))
        measurements.append({"items": count, "old_delta": old_size, "hint": hint_size,
                             "root": root_size, "hint_plus_query": hint_size + query_size})
        assert hint_size < 250 and hint_size < min(old_size, root_size)
        assert_frontier(session)
    assert abs(measurements[1]["hint"] - measurements[0]["hint"]) < 20
    assert measurements[1]["old_delta"] > measurements[0]["old_delta"] * 5
    print("response_lengths=" + json.dumps(measurements))
