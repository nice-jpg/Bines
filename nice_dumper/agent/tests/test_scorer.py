from nice_dumper_agent.models import FunctionRegion, RecognizerResult
from nice_dumper_agent.scorer import bounds_iou, score_regions


def test_bounds_iou_same_bounds() -> None:
    assert bounds_iou("[0,0][100,100]", "[0,0][100,100]") == 1.0


def test_score_full_match_has_full_fidelity() -> None:
    l0 = RecognizerResult([FunctionRegion("[0,0][100,100]", "外卖")])
    l1 = RecognizerResult([FunctionRegion("[0,0][100,100]", "外卖")])

    result = score_regions("x" * 100, "x" * 50, l0, l1)

    assert result.fidelity == 1.0
    assert result.compression == 0.5
    assert result.missing_count == 0
    assert result.score == 90.0


def test_score_missing_label_is_severe_error() -> None:
    l0 = RecognizerResult([FunctionRegion("[0,0][100,100]", "外卖")])
    l1 = RecognizerResult([FunctionRegion("[0,0][100,100]", "酒店")])

    result = score_regions("x" * 100, "x" * 50, l0, l1)

    assert result.fidelity == 0.0
    assert result.missing_count == 1
    assert result.score == -50.0


def test_score_offset_bounds_lower_iou() -> None:
    l0 = RecognizerResult([FunctionRegion("[0,0][100,100]", "外卖")])
    l1 = RecognizerResult([FunctionRegion("[50,0][150,100]", "外卖")])

    result = score_regions("x" * 100, "x" * 100, l0, l1)

    assert round(result.fidelity, 4) == 0.3333
    assert round(result.score, 4) == 26.6667
