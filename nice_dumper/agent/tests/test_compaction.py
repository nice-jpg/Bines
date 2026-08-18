from nice_dumper_agent.compaction import analyze_xml_compaction


def test_compaction_metrics_find_structural_whitespace_and_redundant_attributes() -> None:
    xml = """<?xml version="1.0"?>
    <hierarchy>
      <node index="0" package="demo" text="" clickable="false"
            enabled="true" bounds="[0,0][10,10]">
        <node text="外卖" clickable="true" enabled="false"
              bounds="[0,0][5,5]" />
      </node>
    </hierarchy>
    """

    metrics = analyze_xml_compaction(xml)

    assert metrics["valid_xml"] is True
    assert metrics["xml_declaration_characters"] > 0
    assert metrics["intertag_whitespace_characters"] > 0
    assert metrics["whitespace_text_tail_characters"] > 0
    assert metrics["empty_attribute_count"] == 1
    assert metrics["identity_attribute_count"] == 2
    assert metrics["default_state_attribute_count"] == 2
    assert metrics["candidate_attribute_characters"] > 0
    assert metrics["compression_opportunity_characters"] > 0


def test_compaction_metrics_accept_minified_allowlisted_xml() -> None:
    metrics = analyze_xml_compaction(
        '<hierarchy><node text="外卖" clickable="true" '
        'enabled="false" bounds="[0,0][5,5]"/></hierarchy>'
    )

    assert metrics["valid_xml"] is True
    assert metrics["xml_declaration_characters"] == 0
    assert metrics["intertag_whitespace_characters"] == 0
    assert metrics["whitespace_text_tail_characters"] == 0
    assert metrics["empty_attribute_count"] == 0
    assert metrics["identity_attribute_count"] == 0
    assert metrics["default_state_attribute_count"] == 0


def test_compaction_metrics_report_invalid_xml_without_crashing() -> None:
    metrics = analyze_xml_compaction("<hierarchy>")

    assert metrics["valid_xml"] is False
    assert "parse_error" in metrics


def test_compaction_metrics_find_meaningless_leaves_and_contractible_containers() -> None:
    metrics = analyze_xml_compaction(
        """<hierarchy>
          <node bounds="[0,0][100,100]">
            <node bounds="[0,0][100,100]">
              <node text="外卖" clickable="true" bounds="[0,0][50,50]"/>
            </node>
            <node class="android.view.View" bounds="[50,50][60,60]"/>
          </node>
        </hierarchy>"""
    )

    assert metrics["contractible_container_candidate_count"] == 2
    assert metrics["single_child_container_candidate_count"] == 1
    assert metrics["meaningless_leaf_candidate_count"] == 1
