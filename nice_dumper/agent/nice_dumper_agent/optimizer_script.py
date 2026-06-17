"""Load, validate, and save XML optimizer scripts."""

from __future__ import annotations

import importlib.util
import py_compile
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Callable


DEFAULT_OPTIMIZER_SOURCE = '''"""Current XML optimization script.

The optimizer must be deterministic and must only transform the input string.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET


TRUE_KEYS = ("clickable", "long-clickable", "scrollable", "focusable", "selected")
TEXT_KEYS = ("text", "content-desc")
KEEP_RESOURCE_HINTS = ("tab", "search", "button", "btn", "entry", "channel", "nav", "menu")


def optimize(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return re.sub(r">\\s+<", "><", xml_text).strip()

    nodes = []
    for elem in root.iter():
        if elem.tag == "hierarchy":
            continue
        attrs = elem.attrib
        bounds = attrs.get("bounds", "").strip()
        if not _positive_bounds(bounds):
            continue
        label = _label(attrs)
        resource_id = attrs.get("resource-id", "").strip()
        class_name = attrs.get("class", "").strip()
        true_flags = {key: "true" for key in TRUE_KEYS if attrs.get(key) == "true"}
        if not (label or true_flags or _useful_resource(resource_id) or _useful_class(class_name)):
            continue
        out = {"bounds": bounds}
        for key in TEXT_KEYS:
            value = attrs.get(key, "").strip()
            if value:
                out[key] = re.sub(r"\\s+", " ", value)
        if resource_id and (label or _useful_resource(resource_id)):
            out["resource-id"] = resource_id.rsplit("/", 1)[-1]
        if _useful_class(class_name):
            out["class"] = class_name.rsplit(".", 1)[-1]
        out.update(true_flags)
        nodes.append(out)

    header_attrs = []
    if root.tag == "hierarchy":
        for key in ("rotation", "source"):
            value = root.attrib.get(key)
            if value:
                header_attrs.append((key, value))
    header = "<hierarchy" + _attrs(header_attrs) + ">"
    body = "".join("<node" + _attrs(node.items()) + "/>" for node in nodes)
    return header + body + "</hierarchy>"


def _attrs(items) -> str:
    parts = []
    for key, value in items:
        if value is None or value == "":
            continue
        parts.append(f' {key}="{html.escape(str(value), quote=True)}"')
    return "".join(parts)


def _label(attrs: dict[str, str]) -> str:
    return " ".join(attrs.get(key, "").strip() for key in TEXT_KEYS if attrs.get(key, "").strip()).strip()


def _positive_bounds(value: str) -> bool:
    match = re.match(r"^\\[(\\d+),(\\d+)\\]\\[(\\d+),(\\d+)\\]$", value)
    if not match:
        return False
    left, top, right, bottom = (int(part) for part in match.groups())
    return right > left and bottom > top


def _useful_resource(value: str) -> bool:
    lowered = value.lower()
    return any(hint in lowered for hint in KEEP_RESOURCE_HINTS)


def _useful_class(value: str) -> bool:
    lowered = value.lower()
    return any(hint in lowered for hint in ("button", "edittext", "tab", "checkbox", "switch"))
'''


class OptimizerScriptError(RuntimeError):
    """Raised when an optimizer script is invalid."""


def ensure_optimizer_script(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_OPTIMIZER_SOURCE, encoding="utf-8")


def read_optimizer_source(path: Path) -> str:
    ensure_optimizer_script(path)
    return path.read_text(encoding="utf-8")


def write_optimizer_source(path: Path, source: str) -> None:
    cleaned = _strip_markdown_fence(source)
    validate_optimizer_source(cleaned)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(cleaned, encoding="utf-8")
    tmp_path.replace(path)


def load_optimizer(path: Path) -> Callable[[str], str]:
    ensure_optimizer_script(path)
    validate_optimizer_source(path.read_text(encoding="utf-8"))
    module = _load_module(path)
    optimize = getattr(module, "optimize", None)
    if not callable(optimize):
        raise OptimizerScriptError("optimizer script must define callable optimize(xml_text)")
    return optimize


def validate_optimizer_source(source: str) -> None:
    if "def optimize" not in source:
        raise OptimizerScriptError("optimizer script must define optimize(xml_text)")
    try:
        compile(source, "<optimizer>", "exec")
    except SyntaxError as exc:
        raise OptimizerScriptError(f"optimizer syntax error: {exc}") from exc


def _load_module(path: Path) -> ModuleType:
    py_compile.compile(str(path), doraise=True)
    spec = importlib.util.spec_from_file_location("_nice_dumper_optimizer_script", path)
    if spec is None or spec.loader is None:
        raise OptimizerScriptError(f"cannot load optimizer script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _strip_markdown_fence(source: str) -> str:
    text = textwrap.dedent(str(source or "")).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text + "\n"
