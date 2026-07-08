"""Page mechanism loading, page-local run.py execution, and evaluation."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import posixpath
import traceback
from types import ModuleType
from typing import Any, Mapping


PAGE_MECHANISM_DIR = Path(__file__).resolve().parent / "prompts" / "page_mechanism"


class PageMechanism:
    """Resolve page mechanism files under ``prompts/page_mechanism``."""

    def __init__(self, root: str | Path = PAGE_MECHANISM_DIR) -> None:
        self.root = Path(root).resolve()
        self._states: dict[str, dict[str, Any]] = {}

    def query_page_doc(self, current_path: str) -> str:
        return self._query_doc(current_path, "PAGE.md", "page_doc")

    def query_task_doc(self, current_path: str) -> str:
        return self._query_doc(current_path, "task.md", "task_doc")

    def read_run_py(self, current_path: str) -> str:
        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return self._error_block("run_py_error", current_path, canonical_path, error)
        run_path = page_dir / "run.py"
        if not run_path.is_file():
            return self._error_block("run_py_error", current_path, canonical_path, "run.py was not found.")
        return run_path.read_text(encoding="utf-8")

    def write_run_py(self, current_path: str, content: str) -> str:
        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return self._error_block("run_py_error", current_path, canonical_path, error)
        page_dir.mkdir(parents=True, exist_ok=True)
        run_path = (page_dir / "run.py").resolve()
        if not self._is_relative_to(run_path):
            return self._error_block("run_py_error", current_path, canonical_path, "run.py path escapes page root.")
        run_path.write_text(str(content), encoding="utf-8")
        return json.dumps(
            {"ok": True, "canonical_path": canonical_path, "path": run_path.relative_to(self.root).as_posix()},
            ensure_ascii=False,
            sort_keys=True,
        )

    def sniff_run(self, current_path: str, xml: str) -> str:
        result = self.run_page_script(current_path, xml)
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    def run_page_script(self, current_path: str, xml: str) -> dict[str, Any]:
        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return _script_error("page_path_error", error, canonical_path=canonical_path)
        run_path = page_dir / "run.py"
        if not run_path.is_file():
            return _script_error("missing_run_py", "run.py was not found.", canonical_path=canonical_path)

        try:
            module = _load_run_module(run_path)
            run = getattr(module, "run", None)
            if not callable(run):
                return _script_error("missing_run_function", "run.py must define run(xml).", canonical_path=canonical_path)
            output = run(xml)
            _assert_json_object(output)
        except Exception as exc:  # noqa: BLE001 - script failures are repair signals for collector.
            return {
                "ok": False,
                "canonical_path": canonical_path,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }

        self._states[canonical_path] = dict(output)
        return {"ok": True, "canonical_path": canonical_path, "result": output}

    def state_for(self, current_path: str) -> dict[str, Any] | None:
        _, canonical_path, error = self._page_dir(current_path)
        if error:
            return None
        state = self._states.get(canonical_path)
        return dict(state) if state is not None else None

    def _query_doc(self, current_path: str, filename: str, block_name: str) -> str:
        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return self._error_block(f"{block_name}_error", current_path, canonical_path, error)
        doc = page_dir / filename
        if not doc.is_file():
            return self._error_block(f"{block_name}_error", current_path, canonical_path, f"{filename} was not found.")
        relative_path = doc.relative_to(self.root).as_posix()
        return "\n".join(
            [
                f'<{block_name} canonical_path="{_escape_attr(canonical_path)}">',
                f'<file path="{_escape_attr(relative_path)}">',
                doc.read_text(encoding="utf-8"),
                "</file>",
                f"</{block_name}>",
            ]
        )

    def _page_dir(self, current_path: str) -> tuple[Path, str, str | None]:
        parts, error = normalize_page_path(current_path)
        canonical_path = "/".join(parts)
        if error:
            return self.root, canonical_path, error
        if not parts:
            return self.root, canonical_path, "No page path was provided."
        page_dir = self.root.joinpath(*parts).resolve()
        if not self._is_relative_to(page_dir):
            return self.root, canonical_path, "Page path escapes page mechanism root."
        return page_dir, canonical_path, None

    def _error_block(self, name: str, raw_path: str, attempted_path: str, message: str) -> str:
        lines = [
            f"<{name}>",
            f"<message>{_escape_text(message)}</message>",
            f"<input_path>{_escape_text(raw_path)}</input_path>",
            f"<attempted_path>{_escape_text(attempted_path)}</attempted_path>",
            "<available_paths>",
        ]
        for path in self.available_paths():
            lines.append(f"<path>{_escape_text(path)}</path>")
        lines.extend(["</available_paths>", f"</{name}>"])
        return "\n".join(lines)

    def available_paths(self) -> list[str]:
        paths: list[str] = []
        for doc in self.root.rglob("PAGE.md"):
            if doc.is_file():
                paths.append(doc.parent.relative_to(self.root).as_posix())
        return sorted(paths)

    def _is_relative_to(self, path: Path) -> bool:
        return path == self.root or self.root in path.parents


class PageRunHook:
    """Append page-local run.py output to XML-producing device tool results."""

    def __init__(self, mechanism: PageMechanism | None = None) -> None:
        self.mechanism = mechanism or PageMechanism()
        self.current_path = ""

    def set_current_path(self, current_path: str) -> str:
        parts, error = normalize_page_path(current_path)
        if error:
            return json.dumps({"ok": False, "error": error}, ensure_ascii=False, sort_keys=True)
        self.current_path = "/".join(parts)
        return json.dumps({"ok": True, "current_path": self.current_path}, ensure_ascii=False, sort_keys=True)

    def after_xml(self, xml: str) -> str:
        if not self.current_path:
            return xml
        run_result = self.mechanism.run_page_script(self.current_path, xml)
        return json.dumps(
            {
                "xml": xml,
                "page_path": self.current_path,
                "collection": run_result,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def normalize_page_path(current_path: str) -> tuple[list[str], str | None]:
    raw_path = str(current_path or "").strip().replace("\\", "/")
    if raw_path.startswith("/"):
        return [], "Page path must be relative, not absolute."
    normalized = posixpath.normpath(raw_path).strip("/")
    if normalized in {"", "."}:
        return [], None
    if normalized.startswith("../") or normalized == ".." or any(part == ".." for part in normalized.split("/")):
        return [], "Page path must not contain '..'."
    parts = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return [], None
    parts[0] = _app_slug(parts[0])
    return parts, None


def eval_collection(r1: Any, r2: Any, required_fields: str = "", output_schema: str = "") -> dict[str, Any]:
    expected = _coerce_json(r1)
    actual = _coerce_json(r2)
    if isinstance(actual, Mapping) and actual.get("ok") is True and "result" in actual:
        actual = actual["result"]
    if isinstance(actual, Mapping) and actual.get("ok") is False:
        return {"score": 0, "ok": False, "issues": [f"script_error: {actual.get('error_type')}: {actual.get('error')}"]}

    required = _parse_required_fields(required_fields, output_schema)
    issues: list[str] = []
    _compare(expected, actual, "$", required, issues)
    score = 100 if not issues else max(0, 100 - min(100, len(issues) * 20))
    return {"score": score, "ok": score == 100, "issues": issues}


def _load_run_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_page_run_{abs(hash(path))}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load run.py: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_json_object(value: Any) -> None:
    if not isinstance(value, dict):
        raise TypeError("run.py must return a dict.")
    json.dumps(value, ensure_ascii=False)


def _script_error(error_type: str, error: str, *, canonical_path: str) -> dict[str, Any]:
    return {"ok": False, "canonical_path": canonical_path, "error_type": error_type, "error": error, "traceback": ""}


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _parse_required_fields(required_fields: str, output_schema: str) -> set[str]:
    raw = f"{required_fields}\n{output_schema}"
    fields: set[str] = set()
    for token in raw.replace("{", " ").replace("}", " ").replace("[", " ").replace("]", " ").replace('"', " ").replace("'", " ").split():
        normalized = token.strip(",:")
        if normalized and normalized.replace("_", "").isalnum():
            fields.add(normalized)
    return fields


def _compare(expected: Any, actual: Any, path: str, required: set[str], issues: list[str]) -> None:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            issues.append(f"{path}: expected object")
            return
        keys = set(expected)
        keys.update(key for key in required if key in expected)
        for key in sorted(keys):
            if key not in actual:
                issues.append(f"{path}.{key}: missing")
            else:
                _compare(expected[key], actual[key], f"{path}.{key}", required, issues)
        return
    if isinstance(expected, list):
        if not isinstance(actual, list):
            issues.append(f"{path}: expected list")
            return
        if len(expected) != len(actual):
            issues.append(f"{path}: length expected {len(expected)} actual {len(actual)}")
        for index, expected_item in enumerate(expected[: len(actual)]):
            _compare(expected_item, actual[index], f"{path}[{index}]", required, issues)
        return
    if expected != actual:
        issues.append(f"{path}: expected {expected!r} actual {actual!r}")


def _app_slug(application_name: str) -> str:
    aliases = {
        "com.sankuai.meituan": "meituan",
        "美团": "meituan",
        "meituan": "meituan",
    }
    return aliases.get(application_name.strip(), application_name.strip())


def _escape_text(value: Any) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(value: Any) -> str:
    return _escape_text(value).replace('"', "&quot;")
