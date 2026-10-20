"""Read explicit page strategy declarations without guessing behavior."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.strategy.merchant.state import MERCHANT

if TYPE_CHECKING:
    from src.page.mechanism import PageMechanism
from pathlib import Path


def read_strategy_declaration(path: Path) -> str:
    """Read the restricted YAML front matter without executing YAML constructors."""
    content: str = path.read_text(encoding="utf-8")
    lines: list[str] = content.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"Missing page_strategy declaration: {path}")
    try:
        end: int = lines.index("---", 1)
    except ValueError as exc:
        raise ValueError(f"Unclosed page front matter: {path}") from exc
    fields: list[str] = [
        line.strip() for line in lines[1:end] if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(fields) != 1:
        raise ValueError(f"Expected exactly one page_strategy field: {path}")
    match: re.Match[str] | None = re.fullmatch(
        r"page_strategy:\s*([a-z][a-z_]*|'[a-z][a-z_]*'|\"[a-z][a-z_]*\")", fields[0]
    )
    if match is None:
        raise ValueError(f"Invalid page_strategy declaration: {path}")
    return match[1].strip("\"'")


def read_page_strategy(mechanism: PageMechanism, path: str) -> str:
    """Resolve an explicit declaration; absence never silently selects a strategy."""
    return read_strategy_declaration(mechanism.manual_path(path))


def requires_collection(mechanism: PageMechanism, path: str) -> bool:
    """Support domain calls without a bound visit; bootstrap has no collection."""
    return bool(path) and read_page_strategy(mechanism, path) == MERCHANT
