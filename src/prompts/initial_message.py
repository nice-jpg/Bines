"""Initial user messages and lifecycle targets for application probing."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Final

from src.common.paths import WORKSPACE_ROOT

DEFAULT_SHELL: Final[str] = "zsh"
DEFAULT_TIMEZONE: Final[str] = "Asia/Shanghai"


@dataclass(frozen=True)
class ProbeTaskSpec:
    """Deterministic lifecycle targets derived from workspace config."""

    package_name: str
    target_leaf_paths: tuple[str, ...]


def build_probe_task_spec() -> ProbeTaskSpec:
    """Return the configured package and absolute canonical leaf paths."""

    workspace_path: Path = WORKSPACE_ROOT
    config: ProbeConfiguration = _read_config(workspace_path / "config.xml")
    app_root: str = config.package_name.rsplit(".", 1)[-1] if config.package_name != "未配置" else ""
    paths: list[str] = []
    for city in config.cities:
        for node in city.page_roots:
            _collect_leaf_paths(node, [app_root] if app_root else [], paths)
    return ProbeTaskSpec(
        package_name=config.package_name,
        target_leaf_paths=tuple(dict.fromkeys(path for path in paths if path)),
    )


def build_initial_messages() -> list[str]:
    """Build the first message sent to the agent."""

    workspace_path: Path = WORKSPACE_ROOT
    config_path: Path = workspace_path / "config.xml"
    config: ProbeConfiguration = _read_config(config_path)
    return [
        _build_environment_context(
            cwd=workspace_path,
            shell=DEFAULT_SHELL,
            current_date=date.today().isoformat(),
            timezone=DEFAULT_TIMEZONE,
        ),
        _build_config_context(config),
    ]


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


def _build_config_context(config: ProbeConfiguration) -> str:
    lines: list[str] = [
        "<config_context>",
        f"  - 应用包名：{config.package_name}",
    ]
    if not config.cities:
        lines.extend(
            [
                "    - 城市：未配置",
                "      - 采集参数",
                "        - 地址：未配置",
                "        - 范围：未配置",
                "      - 页面任务树：未配置",
            ]
        )
        return "\n".join(lines)
    for city in config.cities:
        lines.extend(
            [
                f"    - 城市：{city.name}",
                "      - 采集参数",
                f"        - 地址：{city.address}",
                f"        - 范围：{city.range_text}",
                "      - 页面任务树",
            ]
        )
        if city.page_roots:
            for node in city.page_roots:
                _append_page_node(lines, node, indent_level=4, ancestors=[])
        else:
            lines.append("        - 未配置")
    lines.append("</config_context>")
    return "\n".join(lines)


def _append_page_node(
    lines: list[str],
    node: ProbePageNode,
    *,
    indent_level: int,
    ancestors: list[str],
) -> None:
    indent: str = "  " * indent_level
    path_parts: list[str] = [*ancestors, node.name]
    path_text: str = " -> ".join(path_parts)
    if node.kind == "leaf":
        lines.append(f"{indent}- leaf：{node.name}（最终采集目标页面，路径：{path_text}）")
        return
    lines.append(f"{indent}- subpage：{node.name}（当前页面内可点击跳转，路径：{path_text}）")
    for child in node.children:
        _append_page_node(lines, child, indent_level=indent_level + 1, ancestors=path_parts)


def _collect_leaf_paths(node: ProbePageNode, ancestors: list[str], output: list[str]) -> None:
    path_parts: list[str] = [*ancestors, node.name]
    if node.kind == "leaf":
        output.append("/".join(path_parts))
        return
    for child in node.children:
        _collect_leaf_paths(child, path_parts, output)


@dataclass(frozen=True)
class ProbeConfiguration:
    package_name: str = "未配置"
    cities: list[ProbeCityConfiguration] = field(default_factory=lambda: [])


@dataclass(frozen=True)
class ProbeCityConfiguration:
    name: str
    address: str
    range_text: str
    page_roots: list[ProbePageNode]


@dataclass(frozen=True)
class ProbePageNode:
    kind: str
    name: str
    children: list[ProbePageNode]


def _read_config(config_path: Path) -> ProbeConfiguration:
    if not config_path.exists():
        return ProbeConfiguration()

    try:
        root: ET.Element[str] = _parse_config_root(config_path)
    except ET.ParseError:
        return ProbeConfiguration()

    if root.tag != "package":
        return ProbeConfiguration()

    package_name: str = _attribute_value(root, "name")
    cities: list[ProbeCityConfiguration] = [_parse_city(child) for child in root if child.tag == "city"]
    return ProbeConfiguration(package_name=package_name, cities=cities)


def _parse_city(city_element: ET.Element) -> ProbeCityConfiguration:
    children: list[ET.Element[str]] = list(city_element)
    args_element: ET.Element[str] | None = children[0] if children and children[0].tag == "args" else None
    address: str = "未配置"
    range_text: str = "未配置"
    if args_element is not None:
        address_element: ET.Element[str] | None = args_element.find("address")
        range_element: ET.Element[str] | None = args_element.find("range")
        if address_element is not None:
            address = _attribute_value(address_element, "name")
        if range_element is not None:
            range_text = _format_range_text(_attribute_value(range_element, "size"))

    page_roots: list[ProbePageNode] = [
        _parse_page_node(child) for child in children[1:] if child.tag == "subpage"
    ]
    return ProbeCityConfiguration(
        name=_attribute_value(city_element, "name"),
        address=address,
        range_text=range_text,
        page_roots=page_roots,
    )


def _parse_page_node(element: ET.Element) -> ProbePageNode:
    children: list[ProbePageNode] = [
        _parse_page_node(child) for child in element if child.tag in {"subpage", "leaf"}
    ]
    return ProbePageNode(
        kind=element.tag,
        name=_attribute_value(element, "name"),
        children=children,
    )


def _parse_config_root(config_path: Path) -> ET.Element:
    text: str = config_path.read_text(encoding="utf-8")
    if not text.strip():
        raise ET.ParseError("empty config")
    try:
        return ET.fromstring(text)
    except ET.ParseError as original_error:
        clipped: str | None = _clip_first_xml_root(text)
        if clipped is None:
            raise original_error
        return ET.fromstring(clipped)


def _clip_first_xml_root(text: str) -> str | None:
    root_match: re.Match[str] | None = re.search(r"<([^\s>/!?]+)(?:\s[^>]*)?>", text)
    if root_match is None:
        return None
    root_name: str = root_match.group(1)
    end_tag: str = f"</{root_name}>"
    end_index: int = text.find(end_tag, root_match.end())
    if end_index == -1:
        return None
    return text[root_match.start() : end_index + len(end_tag)]


def _format_range_text(range_text: str) -> str:
    value: str = range_text.strip()
    if value != "未配置" and re.fullmatch(r"\d+(?:\.\d+)?", value):
        return f"{value}米"
    return value or "未配置"


def _attribute_value(element: ET.Element, name: str) -> str:
    value: str = element.attrib.get(name, "").strip()
    return value or "未配置"
