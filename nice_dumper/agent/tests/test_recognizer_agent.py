from nice_dumper_agent.recognizer_agent import parse_recognizer_output, recognize_functions_locally


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
