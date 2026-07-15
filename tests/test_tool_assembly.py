from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent import _with_collected_tools


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


def test_custom_common_tool_can_override_default_common_tool() -> None:
    custom = _tool("notify_user")

    result = _with_collected_tools([custom], [_tool("notify_user"), _tool("think")])

    assert result[0] is custom
    assert [tool.name for tool in result] == ["notify_user", "think"]


def test_runtime_managed_tool_cannot_be_overridden() -> None:
    with pytest.raises(ValueError, match="managed by the conductor runtime"):
        _with_collected_tools([_tool("navigate_to")], [_tool("navigate_to")])


def test_duplicate_caller_tool_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate caller-provided tool name"):
        _with_collected_tools([_tool("think"), _tool("think")], [])
