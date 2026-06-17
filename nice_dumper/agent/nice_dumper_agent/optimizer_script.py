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


def optimize(xml_text: str) -> str:
    return xml_text
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
