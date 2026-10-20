"""Checked execution boundary for dynamically loaded page extractors."""

import importlib.util
import inspect
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Protocol, runtime_checkable

from src.common.utils.json import JsonObject, json_object
from src.common.utils.validation import is_string_mapping
from src.device import ReadOnlyPageView


@runtime_checkable
class PageScript(Protocol):
    def run(self, view: ReadOnlyPageView) -> object: ...


class MissingRunFunction(ValueError):
    """The loaded module has no callable run entry point."""


class PageScriptExecutor:
    def execute(self, path: Path, view: ReadOnlyPageView) -> JsonObject:
        spec: ModuleSpec | None = importlib.util.spec_from_file_location(f"_page_run_{abs(hash(path))}", path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load run.agent.py: {path}")
        module: ModuleType = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if not isinstance(module, PageScript) or not callable(module.run):
            raise MissingRunFunction("run.agent.py must define run(view).")
        if list(inspect.signature(module.run).parameters) != ["view"]:
            raise TypeError("run.agent.py must define run(view); migrate the old XML script")
        output: object = module.run(view)
        if not is_string_mapping(output):
            raise TypeError("run.agent.py must return a dict.")
        return json_object(output)
