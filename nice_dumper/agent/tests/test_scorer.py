from nice_dumper_agent.models import FunctionRegion, RecognizerResult
from nice_dumper_agent.recognizer_agent import analyze_hidden_subtrees
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


def test_hidden_subtree_removal_receives_separate_reward() -> None:
    xml0 = _xml_with_hidden_pull_layer()
    xml1 = """<hierarchy bounds="[0,0][100,100]">
      <node resource-id="container" bounds="[0,0][100,100]">
        <node resource-id="t5f" class="android.widget.LinearLayout"
              bounds="[0,0][100,100]">
          <node text="外卖" bounds="[0,0][50,50]" clickable="true" />
          <node text="搜索" bounds="[50,0][100,50]" clickable="true" />
        </node>
      </node>
    </hierarchy>"""
    functions = RecognizerResult(
        [
            FunctionRegion("[0,0][50,50]", "外卖"),
            FunctionRegion("[50,0][100,50]", "搜索"),
        ]
    )

    result = score_regions(
        xml0,
        xml1,
        functions,
        functions,
        hidden_candidates=analyze_hidden_subtrees(xml0),
    )

    assert result.hidden_pruning == 1.0
    assert result.hidden_pruning_reward == 30.0
    assert result.hidden_subtree_count == 1
    assert result.hidden_candidate_count == 3
    assert result.hidden_removed_count == 3


def test_retained_hidden_subtree_receives_no_structural_reward() -> None:
    xml0 = _xml_with_hidden_pull_layer()
    functions = RecognizerResult(
        [
            FunctionRegion("[0,0][50,50]", "外卖"),
            FunctionRegion("[50,0][100,50]", "搜索"),
        ]
    )

    result = score_regions(
        xml0,
        xml0,
        functions,
        functions,
        hidden_candidates=analyze_hidden_subtrees(xml0),
    )

    assert result.hidden_pruning == 0.0
    assert result.hidden_pruning_reward == 0.0
    assert result.hidden_candidate_count == 3
    assert result.hidden_removed_count == 0


def test_removing_only_hidden_root_marker_gets_partial_credit() -> None:
    xml0 = _xml_with_hidden_pull_layer()
    xml1 = xml0.replace('resource-id="pull_loading_bg_container"', 'resource-id=""')
    functions = RecognizerResult([])

    result = score_regions(
        xml0,
        xml1,
        functions,
        functions,
        hidden_candidates=analyze_hidden_subtrees(xml0),
    )

    assert 0.0 < result.hidden_pruning < 1.0
    assert 0.0 < result.hidden_pruning_reward < 30.0
    assert result.hidden_removed_count == 0


def test_removing_hidden_labels_only_does_not_count_nodes_as_removed() -> None:
    xml0 = _xml_with_hidden_pull_layer()
    xml1 = xml0.replace('text="最近使用"', 'text=""').replace(
        'text="我的频道"',
        'text=""',
    )
    functions = RecognizerResult([])

    result = score_regions(
        xml0,
        xml1,
        functions,
        functions,
        hidden_candidates=analyze_hidden_subtrees(xml0),
    )

    assert 0.0 < result.hidden_pruning < 1.0
    assert result.hidden_removed_count == 0
    assert result.hidden_candidate_count == 3


def _xml_with_hidden_pull_layer() -> str:
    return """<hierarchy bounds="[0,0][100,100]">
      <node resource-id="container" bounds="[0,0][100,100]">
        <node resource-id="t5f" class="android.widget.LinearLayout"
              bounds="[0,0][100,100]">
          <node text="外卖" bounds="[0,0][50,50]" clickable="true" />
          <node text="搜索" bounds="[50,0][100,50]" clickable="true" />
        </node>
        <node resource-id="pull_loading_bg_container"
              class="android.widget.FrameLayout" bounds="[0,0][100,100]">
          <node text="最近使用" resource-id="recent_channels"
                bounds="[0,0][50,50]" clickable="false" />
          <node text="我的频道" resource-id="my_channels"
                bounds="[50,0][100,50]" clickable="false" />
        </node>
      </node>
    </hierarchy>"""
