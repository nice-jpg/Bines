"""Initial user message construction for the collection agent."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import xml.etree.ElementTree as ET

DEFAULT_SHELL = "zsh"
DEFAULT_TIMEZONE = "Asia/Shanghai"


@dataclass(frozen=True)
class ProbeTaskSpec:
    """Deterministic lifecycle targets derived from workspace config."""

    package_name: str
    target_leaf_paths: tuple[str, ...]


def build_probe_task_spec(workspace_dir: str | Path | None = None) -> ProbeTaskSpec:
    """Return the configured package and absolute canonical leaf paths."""

    workspace_path = Path(workspace_dir) if workspace_dir is not None else _default_workspace_dir()
    config = _read_config(workspace_path / "config.xml")
    app_root = config.package_name.rsplit(".", 1)[-1] if config.package_name != "未配置" else ""
    paths: list[str] = []
    for city in config.cities:
        for node in city.page_roots:
            _collect_leaf_paths(node, [app_root] if app_root else [], paths)
    return ProbeTaskSpec(
        package_name=config.package_name,
        target_leaf_paths=tuple(dict.fromkeys(path for path in paths if path)),
    )


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
    config = _read_config(config_path)
    return [
        _build_environment_context(
            cwd=workspace_path,
            shell=shell,
            current_date=current_date or date.today().isoformat(),
            timezone=timezone,
        ),
        _build_config_context(config),
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


def _build_config_context(config: "_Config") -> str:
    lines = [
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
    node: "_PageNode",
    *,
    indent_level: int,
    ancestors: list[str],
) -> None:
    indent = "  " * indent_level
    path_parts = [*ancestors, node.name]
    path_text = " -> ".join(path_parts)
    if node.kind == "leaf":
        lines.append(f"{indent}- leaf：{node.name}（最终采集目标页面，路径：{path_text}）")
        return
    lines.append(f"{indent}- subpage：{node.name}（当前页面内可点击跳转，路径：{path_text}）")
    for child in node.children:
        _append_page_node(lines, child, indent_level=indent_level + 1, ancestors=path_parts)


def _collect_leaf_paths(node: "_PageNode", ancestors: list[str], output: list[str]) -> None:
    path_parts = [*ancestors, node.name]
    if node.kind == "leaf":
        output.append("/".join(path_parts))
        return
    for child in node.children:
        _collect_leaf_paths(child, path_parts, output)


@dataclass(frozen=True)
class _Config:
    package_name: str = "未配置"
    cities: list["_CityConfig"] | None = None

    def __post_init__(self) -> None:
        if self.cities is None:
            object.__setattr__(self, "cities", [])


@dataclass(frozen=True)
class _CityConfig:
    name: str
    address: str
    range_text: str
    page_roots: list["_PageNode"]


@dataclass(frozen=True)
class _PageNode:
    kind: str
    name: str
    children: list["_PageNode"]


def _read_config(config_path: Path) -> _Config:
    if not config_path.exists():
        return _Config()

    try:
        root = _parse_config_root(config_path)
    except ET.ParseError:
        return _Config()

    if root.tag != "package":
        return _Config()

    package_name = _attribute_value(root, "name")
    cities = [_parse_city(child) for child in root if child.tag == "city"]
    return _Config(package_name=package_name, cities=cities)


def _parse_city(city_element: ET.Element) -> _CityConfig:
    children = list(city_element)
    args_element = children[0] if children and children[0].tag == "args" else None
    address = "未配置"
    range_text = "未配置"
    if args_element is not None:
        address_element = args_element.find("address")
        range_element = args_element.find("range")
        if address_element is not None:
            address = _attribute_value(address_element, "name")
        if range_element is not None:
            range_text = _format_range_text(_attribute_value(range_element, "size"))

    page_roots = [_parse_page_node(child) for child in children[1:] if child.tag == "subpage"]
    return _CityConfig(
        name=_attribute_value(city_element, "name"),
        address=address,
        range_text=range_text,
        page_roots=page_roots,
    )


def _parse_page_node(element: ET.Element) -> _PageNode:
    children = [
        _parse_page_node(child)
        for child in element
        if child.tag in {"subpage", "leaf"}
    ]
    return _PageNode(
        kind=element.tag,
        name=_attribute_value(element, "name"),
        children=children,
    )


def _parse_config_root(config_path: Path) -> ET.Element:
    text = config_path.read_text(encoding="utf-8")
    if not text.strip():
        raise ET.ParseError("empty config")
    try:
        return ET.fromstring(text)
    except ET.ParseError as original_error:
        clipped = _clip_first_xml_root(text)
        if clipped is None:
            raise original_error
        return ET.fromstring(clipped)


def _clip_first_xml_root(text: str) -> str | None:
    root_match = re.search(r"<([^\s>/!?]+)(?:\s[^>]*)?>", text)
    if root_match is None:
        return None
    root_name = root_match.group(1)
    end_tag = f"</{root_name}>"
    end_index = text.find(end_tag, root_match.end())
    if end_index == -1:
        return None
    return text[root_match.start() : end_index + len(end_tag)]


def _format_range_text(range_text: str) -> str:
    value = range_text.strip()
    if value != "未配置" and re.fullmatch(r"\d+(?:\.\d+)?", value):
        return f"{value}米"
    return value or "未配置"


def _attribute_value(element: ET.Element, name: str) -> str:
    value = element.attrib.get(name, "").strip()
    return value or "未配置"


def _default_workspace_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "workspace"
