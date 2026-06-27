from nice_dumper_agent.recognizer_agent import (
    analyze_hidden_subtrees,
    parse_recognizer_output,
    prepare_recognizer_xml,
    recognize_functions_locally,
)


def test_parse_valid_recognizer_output() -> None:
    result = parse_recognizer_output('{"functions":[{"bounds":"[1,2][3,4]","label":"外卖"}]}')

    assert result.ok
    assert result.functions[0].bounds == "[1,2][3,4]"
    assert result.functions[0].label == "外卖"


def test_parse_rejects_invalid_json() -> None:
    result = parse_recognizer_output("not json")

    assert not result.ok
    assert "invalid recognizer JSON" in str(result.error)


def test_parse_rejects_invalid_bounds() -> None:
    result = parse_recognizer_output('{"functions":[{"bounds":"[3,4][1,2]","label":"外卖"}]}')

    assert not result.ok
    assert "invalid bounds" in str(result.error)


def test_parse_accepts_empty_function_list() -> None:
    result = parse_recognizer_output('{"functions":[]}')

    assert result.ok
    assert result.functions == []


def test_local_recognizer_extracts_clickable_labeled_nodes() -> None:
    xml = """<hierarchy>
      <node text="外卖" bounds="[10,20][110,120]" clickable="true" />
      <node text="" content-desc="搜索" bounds="[0,0][300,80]" clickable="true" />
      <node text="装饰" bounds="[0,0][10,10]" clickable="false" />
    </hierarchy>"""

    result = recognize_functions_locally(xml)

    assert result.ok
    assert [(item.bounds, item.label) for item in result.functions] == [
        ("[10,20][110,120]", "外卖"),
        ("[0,0][300,80]", "搜索"),
    ]


def test_recognizer_removes_explicitly_invisible_subtree() -> None:
    xml = """<hierarchy bounds="[0,0][100,100]">
      <node resource-id="main" bounds="[0,0][100,100]" visible-to-user="true">
        <node text="外卖" bounds="[0,0][50,50]" clickable="true" />
      </node>
      <node resource-id="drawer" bounds="[0,0][100,100]" visible-to-user="false">
        <node text="隐藏入口" bounds="[0,0][50,50]" clickable="true" />
      </node>
    </hierarchy>"""

    result = recognize_functions_locally(xml)

    assert result.ok
    assert [item.label for item in result.functions] == ["外卖"]


def test_recognizer_removes_inactive_preloaded_pull_layer_from_legacy_xml() -> None:
    xml = """<hierarchy bounds="[0,0][1080,2400]">
      <node resource-id="container" bounds="[0,0][1080,2263]">
        <node resource-id="main" bounds="[0,0][1080,2263]">
          <node text="外卖" bounds="[0,0][500,500]" clickable="true" />
          <node text="搜索" bounds="[500,0][1080,500]" clickable="true" />
        </node>
        <node resource-id="com.example:id/pull_loading_bg_container"
              bounds="[0,0][1080,2263]">
          <node content-desc="最近使用" resource-id="com.example:id/channel"
                bounds="[100,100][500,300]" clickable="false" />
        </node>
      </node>
    </hierarchy>"""

    prepared = prepare_recognizer_xml(xml)
    result = recognize_functions_locally(xml)

    assert "pull_loading_bg_container" not in prepared
    assert "最近使用" not in prepared
    assert [item.label for item in result.functions] == ["外卖", "搜索"]


def test_hidden_subtree_analysis_exposes_optimizer_evidence() -> None:
    xml = """<hierarchy bounds="[0,0][1080,2400]">
      <node resource-id="container" bounds="[0,0][1080,2263]">
        <node resource-id="t5f" bounds="[0,0][1080,2263]">
          <node text="外卖" bounds="[0,0][500,500]" clickable="true" />
          <node text="搜索" bounds="[500,0][1080,500]" clickable="true" />
        </node>
        <node resource-id="pull_loading_bg_container" bounds="[0,0][1080,2263]">
          <node text="最近使用" bounds="[100,100][500,300]" clickable="false" />
        </node>
      </node>
    </hierarchy>"""

    candidates = analyze_hidden_subtrees(xml)

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.resource_id == "pull_loading_bg_container"
    assert candidate.reason == "inactive-preloaded-pull-layer"
    assert candidate.actionable_descendant_count == 0
    assert candidate.overlapping_sibling_actionable_count == 2
    assert candidate.estimated_characters > 100
    assert "最近使用" in candidate.sample_labels


def test_explicit_visible_pull_layer_is_not_removed() -> None:
    xml = """<hierarchy bounds="[0,0][1080,2400]">
      <node resource-id="container" bounds="[0,0][1080,2263]">
        <node resource-id="main" bounds="[0,0][1080,2263]">
          <node text="外卖" bounds="[0,0][500,500]" clickable="true" />
          <node text="搜索" bounds="[500,0][1080,500]" clickable="true" />
        </node>
        <node resource-id="com.example:id/pull_loading_bg_container"
              bounds="[0,0][1080,2263]" visible-to-user="true">
          <node content-desc="最近使用" resource-id="com.example:id/channel"
                bounds="[100,100][500,300]" clickable="false" />
        </node>
      </node>
    </hierarchy>"""

    prepared = prepare_recognizer_xml(xml)

    assert "pull_loading_bg_container" in prepared
    assert "最近使用" in prepared


def test_bounds_and_sibling_order_alone_do_not_hide_active_nodes() -> None:
    xml = """<hierarchy bounds="[0,0][100,100]">
      <node resource-id="main" bounds="[0,0][100,100]">
        <node text="首页" bounds="[0,0][50,50]" clickable="true" />
      </node>
      <node resource-id="dialog" bounds="[0,0][100,100]">
        <node text="确认" bounds="[0,0][50,50]" clickable="true" />
      </node>
    </hierarchy>"""

    result = recognize_functions_locally(xml)

    assert result.ok
    assert [item.label for item in result.functions] == ["首页", "确认"]
