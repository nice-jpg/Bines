"""View-based search frontiers; node statistics remain diagnostic only."""

from dataclasses import FrozenInstanceError
import json
from unittest.mock import patch

import pytest
from Reco.model_interface import PageSession
from src.device.services.page.bridge import PageBridge
from src.device.services.page.change_hint import change_hint


def button(text, kind="Button", bounds=""):
    return f'<node class="android.widget.{kind}" text="{text}" bounds="{bounds}"/>'


def container(children, kind="ScrollView", text="", bounds=""):
    return f'<node class="android.widget.{kind}" text="{text}" bounds="{bounds}">{children}</node>'


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


def visible(children):
    return page(container(children, bounds="[0,0][100,100]"))


def assert_frontier(session):
    hint = change_hint(session)
    assert set(hint) == {"kind", "node_update_ratio", "view_update_ratio", "regions"}
    root = next(key for key, node in session.last_sync_stats.items() if node.parent_id is None)
    assert hint["node_update_ratio"] == session.node_update_ratio(root)
    assert hint["view_update_ratio"] == session.view_update_ratio(root)
    selected = ids(session)
    assert len(selected) <= 3
    assert len(selected) == len(set(selected))
    for region in hint["regions"]:
        assert set(region) == {"id", "node_update_ratio", "view_update_ratio"}
        assert session.call("page_node", {"key": region["id"]})["id"] == region["id"]
        assert region["node_update_ratio"] == session.node_update_ratio(region["id"])
        assert region["view_update_ratio"] == session.view_update_ratio(region["id"]) > 0
        parent = session.last_sync_stats[region["id"]].parent_id
        while parent is not None:
            assert parent not in selected
            parent = session.last_sync_stats[parent].parent_id
    assert change_hint(session) == hint


def test_text_changes_preserve_stats_but_do_not_select_regions():
    session = synced(visible(button("old", bounds="[0,0][20,20]")),
                     visible(button("new", bounds="[0,0][20,20]")))
    root, group, leaf = (session.last_sync_stats[i] for i in range(3))
    assert not root.self_changed and not group.self_changed and leaf.self_changed
    assert (root.changed_count, root.compared_count) == (1, 3)
    assert change_hint(session)["node_update_ratio"] == pytest.approx(1 / 3)
    assert change_hint(session)["view_update_ratio"] == 0
    assert ids(session) == []
    with pytest.raises(FrozenInstanceError):
        leaf.changed_count = 10
    with pytest.raises(TypeError):
        session.last_sync_stats[2] = root
    assert_frontier(session)


def test_root_ratio_and_refresh_use_view_even_when_node_ratio_is_different():
    before = visible(button("old", bounds="[0,0][20,20]"))
    after = visible(button("new", bounds="[0,0][10,20]"))
    source = iter([before, after, after])
    bridge = PageBridge(lambda: next(source))
    assert bridge.root_change_ratio() is None
    initial, _ = bridge.refresh()
    assert "kind" not in initial
    assert bridge.root_change_ratio() is None
    hint, _ = bridge.refresh()
    assert hint["view_update_ratio"] == bridge.root_change_ratio() == .02
    assert hint["node_update_ratio"] == pytest.approx(1 / 3)
    assert ids(bridge._session) == [2]
    assert_frontier(bridge._session)
    bridge.refresh()
    assert bridge.root_change_ratio() == 0


def test_growth_does_not_use_saturated_node_ratio_as_view_evidence():
    session = synced(visible(items(2)), visible(items(20, "new")))
    assert session.node_update_ratio(0) == 1
    assert change_hint(session)["view_update_ratio"] == 0
    assert ids(session) == []


def test_view_ranking_is_bounded_and_ties_use_tree_order():
    before = visible(''.join(button(str(i), bounds=f'[{i*20},0][{i*20+20},20]') for i in range(4)))
    after = visible(''.join(button(str(i), bounds=f'[{i*20},0][{i*20+10},20]') for i in range(4)))
    session = synced(before, after)
    assert ids(session) == [2, 3, 4]
    assert_frontier(session)


def test_larger_view_ratio_wins_despite_equal_node_counts():
    before = visible(''.join(button(str(i), bounds=f'[{i*20},0][{i*20+20},20]') for i in range(4)))
    after = visible(''.join(button(str(i), bounds=f'[{i*20},0][{i*20+(5 if i == 3 else 10)},20]') for i in range(4)))
    session = synced(before, after)
    assert ids(session) == [2, 3, 5]
    assert_frontier(session)


@pytest.mark.parametrize('replacement', ['', button('new', kind='ImageView', bounds='[0,0][20,20]')])
def test_deleted_and_replaced_regions_anchor_at_surviving_parent(replacement):
    session = synced(visible(button('old', bounds='[0,0][20,20]')), visible(replacement))
    assert ids(session) == [1]
    assert change_hint(session)['view_update_ratio'] == .04
    assert not set(ids(session)) & session._deleted_ids.keys()
    assert_frontier(session)


def test_offscreen_changes_do_not_select_regions():
    session = synced(visible(button('old', bounds='[0,110][20,150]')),
                     visible(button('new', bounds='[0,110][40,150]')))
    assert session.node_update_ratio(0) > 0
    assert ids(session) == []


def test_zero_whole_page_change_does_not_select_descendant_changes():
    # Parent grows outside the fixed viewport: its contribution is zero and
    # replaces its child's contribution under the view metric's stop rule.
    session = synced(visible(button('a', bounds='[0,0][20,20]')),
                     page(container(button('a', bounds='[0,0][10,20]'), bounds='[0,0][200,100]')))
    assert session.view_update_ratio(2) == .5
    assert session.view_update_ratio(0) == 0
    assert ids(session) == []


def test_failed_sync_keeps_previous_view_evidence():
    session = synced(visible(button('a', bounds='[0,0][20,20]')),
                     visible(button('a', bounds='[0,0][10,20]')))
    previous = session.last_sync_stats
    previous_hint = change_hint(session)
    with pytest.raises(ValueError):
        session.sync(session)
    assert session.last_sync_stats is previous
    assert change_hint(session) == previous_hint


def test_hint_is_bounded_and_does_not_serialize_changed_content():
    sizes = []
    for count in (30, 300):
        before = visible(''.join(button('old' * count, bounds='[0,0][20,20]') for _ in range(count)))
        after = visible(''.join(button('new' * count, bounds='[0,0][10,20]') for _ in range(count)))
        session = synced(before, after)
        hint = change_hint(session)
        assert_frontier(session)
        with patch.object(session, 'call', side_effect=AssertionError('unexpected query')):
            assert change_hint(session) == hint
        sizes.append(len(json.dumps(hint)))
        assert sizes[-1] < 400
    assert abs(sizes[1] - sizes[0]) < 20
