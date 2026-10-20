from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from src.device.services.page.similarity import PageSimilarityError, PageSimilarityEvaluator


def _xml(*labels: str) -> str:
    nodes = "".join(
        f'<n b="[0,{index * 100}][100,{index * 100 + 80}]" t="{label}" c="TextView"/>'
        for index, label in enumerate(labels)
    )
    return f"<h>{nodes}</h>"


def test_identical_pages_have_full_similarity() -> None:
    evaluator = PageSimilarityEvaluator()
    assert evaluator.compare(_xml("header", "item-a", "footer"), _xml("header", "item-a", "footer")).similarity == 1.0


def test_semantically_different_pages_are_below_navigation_threshold() -> None:
    evaluator = PageSimilarityEvaluator()
    result = evaluator.compare(_xml("home", "food", "profile"), _xml("merchant", "cup", "cart"))
    assert result.similarity < 0.5


def test_scroll_with_sticky_content_has_partial_similarity() -> None:
    evaluator = PageSimilarityEvaluator()
    before = _xml("header", "item-a", "item-b", "item-c", "cart")
    after = _xml("header", "item-c", "item-d", "item-e", "cart")
    result = evaluator.compare(before, after)
    assert 0.1 <= result.similarity <= 0.9


def test_duplicate_nodes_are_counted_as_a_multiset() -> None:
    evaluator = PageSimilarityEvaluator()
    result = evaluator.compare(_xml("item", "item"), _xml("item"))
    assert result.similarity < 1.0


@pytest.mark.parametrize("xml", ["", "not xml", "<h/>"])
def test_unusable_page_xml_is_rejected(xml: str) -> None:
    with pytest.raises(PageSimilarityError):
        PageSimilarityEvaluator().compare(xml, _xml("valid"))
