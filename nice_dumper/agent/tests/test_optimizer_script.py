from pathlib import Path

import pytest

from nice_dumper_agent.optimizer_script import (
    OptimizerScriptError,
    ensure_optimizer_script,
    load_optimizer,
    write_optimizer_source,
)


def test_initial_optimizer_aggressively_keeps_function_nodes(tmp_path: Path) -> None:
    path = tmp_path / "optimize_xml.py"
    ensure_optimizer_script(path)

    optimize = load_optimizer(path)
    xml = """<hierarchy rotation="0" source="ui-automation">
      <node index="0" text="外卖" resource-id="com.demo/id/takeout_entry" class="android.widget.TextView"
            package="demo" bounds="[1,2][101,102]" clickable="true" enabled="true"/>
      <node index="1" text="" package="demo" bounds="[0,0][10,10]" clickable="false"/>
    </hierarchy>"""
    result = optimize(xml)

    assert "外卖" in result
    assert "bounds=\"[1,2][101,102]\"" in result
    assert "clickable=\"true\"" in result
    assert "package=" not in result
    assert "index=" not in result


def test_write_rejects_syntax_error(tmp_path: Path) -> None:
    path = tmp_path / "optimize_xml.py"

    with pytest.raises(OptimizerScriptError):
        write_optimizer_source(path, "def optimize(:\n")


def test_write_rejects_missing_optimize(tmp_path: Path) -> None:
    path = tmp_path / "optimize_xml.py"

    with pytest.raises(OptimizerScriptError):
        write_optimizer_source(path, "def other(xml_text):\n    return xml_text\n")


def test_write_accepts_markdown_fence(tmp_path: Path) -> None:
    path = tmp_path / "optimize_xml.py"

    write_optimizer_source(path, "```python\ndef optimize(xml_text: str) -> str:\n    return xml_text.strip()\n```")
    optimize = load_optimizer(path)

    assert optimize(" abc ") == "abc"
