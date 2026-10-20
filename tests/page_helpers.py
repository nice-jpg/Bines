"""Create page services under isolated test directories."""

from pathlib import Path
from unittest.mock import patch

from src.collector.history import CollectorHistory
from src.page.mechanism import PageMechanism


def page_mechanism(root: Path) -> PageMechanism:
    for manual in root.rglob("PAGE.md"):
        if not manual.read_text().startswith("---\n"):
            kind = "merchant" if manual.parent.relative_to(root).as_posix() in {"meituan/美食/商家", "meituan/外卖/美食/商家"} else "general"
            manual.write_text(f"---\npage_strategy: {kind}\n---\n\n" + manual.read_text())
    with patch("src.page.mechanism.PAGE_MECHANISM_DIR", root):
        return PageMechanism()


def collector_history(root: Path) -> CollectorHistory:
    with patch("src.collector.history.COLLECTOR_HISTORY_ROOT", root):
        return CollectorHistory()
