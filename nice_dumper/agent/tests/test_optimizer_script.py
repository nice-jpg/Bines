from pathlib import Path

import pytest

from nice_dumper_agent.optimizer_script import (
    OptimizerScriptError,
    ensure_optimizer_script,
    load_optimizer,
    write_optimizer_source,
)


def test_initial_optimizer_returns_input(tmp_path: Path) -> None:
    path = tmp_path / "optimize_xml.py"
    ensure_optimizer_script(path)

    optimize = load_optimizer(path)

    assert optimize("<hierarchy />") == "<hierarchy />"


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
