import pytest

from src.collector.evaluation import eval_collection


@pytest.mark.parametrize(("baseline", "actual", "passes"), [
    ({"items": [{"name": "cup"}]},
     {"items": [{"name": "bowl"}, {"name": "large cup", "price": 10}]}, True),
    ({"items": [{"name": "cup"}, {"name": "cup"}]},
     {"items": [{"name": "cup"}]}, False),
    ({"items": [{"name": "cup"}, {"name": "large cup"}]},
     {"items": [{"name": "large cup"}, {"name": "small cup"}]}, True),
    ({"items": [{"name": "cup", "price": 10}]},
     {"items": [{"name": "cup", "price": 20}, {"name": "bowl", "price": 10}]}, False),
    ({"items": [{"name": "large cup"}]}, {"items": [{"name": "cup"}]}, False),
    ({"items": [{"price": 10}]}, {"items": [{"price": 10.0}]}, True),
    ({"items": [{"price": 1}]}, {"items": [{"price": True}]}, False),
    ({"items": [{"name": "cup"}]}, {"items": [{}]}, False),
    ({"items": []}, {"items": [{"name": "cup"}]}, True),
])
def test_visual_evidence_is_an_injective_subset(baseline: object, actual: object, passes: bool) -> None:
    assert (eval_collection(baseline, actual)["score"] == 100) is passes


def test_script_execution_error_never_passes() -> None:
    assert eval_collection({"items": []}, {"ok": False, "error": "failed"})["score"] == 0


@pytest.mark.parametrize(("visible", "extracted"), [
    (" A\tB\nC\u3000D\u00a0E ", "ABCDE"),
    ("，。（）－；", ",.()-;"),
    (",.()-;", "，．（）—；"),
    ("面（小份），微 辣；", "招牌面(小份),微辣;加蛋"),
])
def test_visual_typography_is_normalized(visible: str, extracted: str) -> None:
    baseline = {"items": [{"name": visible}]}
    actual = {"items": [{"name": extracted}]}
    assert eval_collection(baseline, actual)["score"] == 100
    assert baseline["items"][0]["name"] == visible
    assert actual["items"][0]["name"] == extracted


@pytest.mark.parametrize(("visible", "extracted"), [("a/b", "ab"), ("A", "a"), ("12", "1.2")])
def test_other_text_differences_remain_significant(visible: str, extracted: str) -> None:
    assert eval_collection({"name": visible}, {"name": extracted})["score"] < 100
