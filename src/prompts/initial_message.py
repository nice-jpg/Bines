"""Initial user message construction for the collection agent."""

from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import xml.etree.ElementTree as ET

DEFAULT_SHELL = "zsh"
DEFAULT_TIMEZONE = "Asia/Shanghai"


def build_initial_messages(
    workspace_dir: str | Path | None = None,
    *,
    shell: str = DEFAULT_SHELL,
    current_date: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> list[str]:
    """Build the first message sent to the agent."""

    workspace_path = Path(workspace_dir) if workspace_dir is not None else _default_workspace_dir()
    config_path = workspace_path / "config.xml"
    return [
        _build_environment_context(
            cwd=workspace_path,
            shell=shell,
            current_date=current_date or date.today().isoformat(),
            timezone=timezone,
        ),
        _build_config_context(config_path),
    ]


def build_initial_message(
    workspace_dir: str | Path | None = None,
    *,
    shell: str = DEFAULT_SHELL,
    current_date: str | None = None,
    timezone: str = DEFAULT_TIMEZONE,
) -> str:
    """Build the initial messages as one text block for legacy callers."""

    return "\n\n".join(
        build_initial_messages(
            workspace_dir,
            shell=shell,
            current_date=current_date,
            timezone=timezone,
        )
    )


def _build_environment_context(cwd: Path, shell: str, current_date: str, timezone: str) -> str:
    return "\n".join(
        [
            "<environment_context>",
            f"  <cwd>{cwd}</cwd>",
            f"  <shell>{shell}</shell>",
            f"  <current_date>{current_date}</current_date>",
            f"  <timezone>{timezone}</timezone>",
            "</environment_context>",
        ]
    )


def _build_config_context(config_path: Path) -> str:
    config = _read_config(config_path)
    lines = [
        "<config_context>",
        f"  - 应用名称：{config.application_name}",
        f"    - 城市：{config.city}",
        "    - 采集参数",
        f"      - 地址：{config.address}",
        f"      - 范围：{config.range_text}",
    ]
    for index, page in enumerate(config.secondary_pages, start=1):
        lines.append(f"    - 二级页面{index}：{page}")
    if not config.secondary_pages:
        lines.extend(["    - 二级页面1：未配置", "    - 二级页面2：未配置"])
    lines.append("</config_context>")
    return "\n".join(lines)


class _Config:
    def __init__(
        self,
        *,
        application_name: str = "未配置",
        city: str = "未配置",
        address: str = "未配置",
        range_text: str = "未配置",
        secondary_pages: list[str] | None = None,
    ) -> None:
        self.application_name = application_name
        self.city = city
        self.address = address
        self.range_text = range_text
        self.secondary_pages = secondary_pages or []


def _read_config(config_path: Path) -> _Config:
    if not config_path.exists() or not config_path.read_text(encoding="utf-8").strip():
        return _Config()

    try:
        root = ET.parse(config_path).getroot()
    except ET.ParseError:
        return _Config()

    return _Config(
        application_name=_node_value(root, ["package", "应用名称", "app_name", "application_name", "application", "app", "name"]),
        city=_node_value(root, ["city", "城市"]),
        address=_node_value(root, ["address", "地址"]),
        range_text=_node_value(root, ["range", "范围", "radius", "scope"], attribute_names=["size", "name", "value"]),
        secondary_pages=_secondary_pages(root),
    )


def _node_value(root: ET.Element, names: list[str], attribute_names: list[str] | None = None) -> str:
    wanted = {_normalize_name(name) for name in names}
    attributes = [_normalize_name(name) for name in (attribute_names or ["name", "value"])]
    for element in root.iter():
        if _normalize_name(element.tag) in wanted:
            value = _element_value(element, attributes)
            if value:
                return value
        for key, value in element.attrib.items():
            if _normalize_name(key) in wanted and value.strip():
                return value.strip()
    return "未配置"


def _secondary_pages(root: ET.Element) -> list[str]:
    pages: list[str] = []
    for element in root.iter():
        normalized = _normalize_name(element.tag)
        text = _element_value(element, ["name", "value"])
        if not text:
            continue
        if normalized.startswith("二级页面") or normalized in {"subpage", "secondary_page", "secondarypage", "level2page"}:
            pages.append(text)
            continue
        if element.attrib.get("level") == "2" and normalized in {"page", "页面"}:
            pages.append(text)
    return pages


def _element_value(element: ET.Element, attribute_names: list[str]) -> str:
    for attribute_name in attribute_names:
        for key, value in element.attrib.items():
            if _normalize_name(key) == attribute_name and value.strip():
                return value.strip()
    return _element_text(element)


def _element_text(element: ET.Element) -> str:
    return " ".join(text.strip() for text in element.itertext() if text and text.strip())


def _normalize_name(name: str) -> str:
    return re.sub(r"[\s\-_.]", "", str(name).strip().lower())


def _default_workspace_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "workspace"
