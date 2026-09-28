"""Page mechanism loading, page-local run.agent.py execution, and evaluation."""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import posixpath
import re
from tempfile import NamedTemporaryFile
from threading import Lock
import traceback
from types import ModuleType
from typing import Any, Mapping

from codex_apply_patch import apply_patch_in_memory, parse_patch

from src.page_view import ReadOnlyPageView
from src.collection_contract import CollectionContract


PAGE_MECHANISM_DIR = Path(__file__).resolve().parent / "prompts" / "page_mechanism"
RUN_AGENT_PY_PATCH_DESCRIPTION_RE = re.compile(
    r"UpdateFile: run\.agent\.py \([1-9]\d* chunks\)"
)


@dataclass(frozen=True)
class PagePolicy:
    """Deterministic runtime policy for one canonical page class."""

    role: str = "page"
    requires_collector: bool = False
    max_product_entry_taps: int = 0


@dataclass
class CollectorTrainingRecord:
    """Training and archival state for one concrete page visit."""

    visit_id: str
    page_path: str
    trained: bool = False
    merchant_profile: dict[str, Any] = field(default_factory=dict)
    script_fingerprint: str = ""
    restoration: dict[str, Any] = field(default_factory=dict)
    contract_fingerprint: str = ""


class PageMechanism:
    """Resolve page mechanism files under ``prompts/page_mechanism``."""

    def __init__(
        self,
        root: str | Path = PAGE_MECHANISM_DIR,
        merchant_page_paths: set[str] | None = None,
        page_policies: Mapping[str, PagePolicy] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self._states: dict[str, dict[str, Any]] = {}
        self._run_patch_lock = Lock()
        configured_paths = merchant_page_paths if merchant_page_paths is not None else {
            "meituan/美食/商家",
            "meituan/外卖/美食/商家",
        }
        merchant_paths = {
            "/".join(parts)
            for path in configured_paths
            for parts, error in [normalize_page_path(path)]
            if not error and parts
        }
        self._page_policies = {
            path: PagePolicy(role="merchant", requires_collector=True, max_product_entry_taps=1)
            for path in merchant_paths
        }
        for path, policy in (page_policies or {}).items():
            parts, error = normalize_page_path(path)
            if error or not parts:
                raise ValueError(error or "Page policy path is required.")
            self._page_policies["/".join(parts)] = policy

    def query_page_doc(self, current_path: str) -> str:
        return self._query_doc(current_path, "PAGE.md", "page_doc")

    def _manual_section(self, current_path, section, content=None):
        canonical = self.validate_page_manual(current_path)
        target = (self.root / canonical / "PAGE.md").resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Manual escapes page root")
        start, end = f"<!-- {section}:begin -->", f"<!-- {section}:end -->"
        with self._run_patch_lock:
            original = target.read_text(encoding="utf-8")
            if original.count(start) != original.count(end) or original.count(start) > 1:
                raise ValueError("Invalid manual section markers")
            if start in original and original.index(start) >= original.index(end):
                raise ValueError("Reversed manual section markers")
            old = original.split(start, 1)[1].split(end, 1)[0].strip() if start in original else None
            if content is None:
                return old
            if "<!--" in content or "-->" in content:
                raise ValueError("Section content cannot contain control markers")
            if old == content.strip():
                return False
            block = f"{start}\n{content.strip()}\n{end}"
            updated = (original.split(start, 1)[0] + block + original.split(end, 1)[1]
                       if old is not None else original.rstrip() + "\n\n" + block + "\n")
            temporary = None
            try:
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, delete=False) as handle:
                    temporary = handle.name
                    handle.write(updated)
                os.replace(temporary, target)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
            return True

    def update_page_notes(self, current_path: str, content: str) -> bool:
        return self._manual_section(current_path, "operation-notes", "## Operation Observations\n" + content)

    def collection_contract(self, current_path: str) -> CollectionContract | None:
        content = self._manual_section(current_path, "collection-contract")
        if content is None:
            return None
        match = re.search(r"```json\s*\n(.*?)\n```", content, re.DOTALL)
        if match is None:
            raise ValueError("Collection Requirements must contain a JSON block")
        return CollectionContract.from_dict(json.loads(match.group(1)))

    def save_collection_contract(self, current_path, contract):
        content = "## Collection Requirements\n```json\n" + json.dumps(contract.as_dict(), sort_keys=True) + "\n```"
        return self._manual_section(current_path, "collection-contract", content)

    def query_task_doc(self, current_path: str) -> str:
        return self._query_doc(current_path, "task.md", "task_doc")

    def load_manual_bundle(self, current_path: str) -> str:
        """Strictly load PAGE.md followed by task.md for one canonical page."""

        canonical_path = self.validate_page_manual(current_path)
        return "\n".join(
            [
                f'<current_page_manual canonical_path="{_escape_attr(canonical_path)}">',
                self.query_page_doc(canonical_path),
                self.query_task_doc(canonical_path),
                "</current_page_manual>",
            ]
        )

    def home_path_for_package(self, package_name: str) -> str:
        """Resolve and validate the page-mechanism root for an app package."""

        parts, error = normalize_page_path(package_name)
        if error or not parts:
            raise ValueError(error or "package_name is required.")
        return self.validate_page_manual(parts[0])

    def validate_page_manual(self, current_path: str) -> str:
        """Return the canonical path when both required page documents exist."""

        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            raise ValueError(error)
        missing = [name for name in ("PAGE.md", "task.md") if not (page_dir / name).is_file()]
        if missing:
            raise FileNotFoundError(
                f"Page {canonical_path!r} is missing required manual files: {', '.join(missing)}"
            )
        return canonical_path

    def read_run_agent_py(self, current_path: str) -> str:
        snapshot = self.read_run_agent_py_snapshot(current_path)
        if snapshot["ok"] is True:
            return str(snapshot["content"])
        return self._error_block(
            "run_agent_py_error",
            current_path,
            str(snapshot["canonical_path"]),
            str(snapshot["error"]),
        )

    def read_run_agent_py_snapshot(self, current_path: str) -> dict[str, Any]:
        """Read run.agent.py with a structured error contract for internal tools."""

        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return {
                "ok": False,
                "canonical_path": canonical_path,
                "error_type": "RunAgentPyPathError",
                "error": error,
            }
        run_path = (page_dir / "run.agent.py").resolve()
        if not self._is_relative_to(run_path):
            return {
                "ok": False,
                "canonical_path": canonical_path,
                "error_type": "RunAgentPyPathError",
                "error": "run.agent.py path escapes page root.",
            }
        if not run_path.is_file():
            return {
                "ok": False,
                "canonical_path": canonical_path,
                "error_type": "MissingRunAgentPy",
                "error": "run.agent.py was not found.",
            }
        return {
            "ok": True,
            "canonical_path": canonical_path,
            "path": run_path.relative_to(self.root).as_posix(),
            "content": run_path.read_text(encoding="utf-8"),
        }

    def write_run_agent_py(self, current_path: str, content: str) -> str:
        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return self._error_block("run_agent_py_error", current_path, canonical_path, error)
        page_dir.mkdir(parents=True, exist_ok=True)
        run_path = (page_dir / "run.agent.py").resolve()
        if not self._is_relative_to(run_path):
            return self._error_block(
                "run_agent_py_error",
                current_path,
                canonical_path,
                "run.agent.py path escapes page root.",
            )
        run_path.write_text(str(content), encoding="utf-8")
        return json.dumps(
            {"ok": True, "canonical_path": canonical_path, "path": run_path.relative_to(self.root).as_posix()},
            ensure_ascii=False,
            sort_keys=True,
        )

    def apply_run_agent_py_patch(self, current_path: str, patch: str) -> str:
        """Apply one context patch to run.agent.py after in-memory validation."""

        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return _patch_error("PatchScopeError", error, canonical_path)
        run_path = (page_dir / "run.agent.py").resolve()
        if not self._is_relative_to(run_path) or not run_path.is_file():
            return _patch_error(
                "PatchScopeError",
                "The current page must contain an existing run.agent.py.",
                canonical_path,
            )

        with self._run_patch_lock:
            current_content = run_path.read_text(encoding="utf-8")
            current_fingerprint = _fingerprint_text(current_content)
            try:
                descriptions = parse_patch(str(patch))
            except Exception as exc:  # noqa: BLE001 - extension exposes parse failures as RuntimeError.
                return _patch_error(
                    "InvalidPatchFormat",
                    str(exc).strip() or "The patch does not follow the Codex patch format.",
                    canonical_path,
                    script_fingerprint=current_fingerprint,
                )
            if (
                len(descriptions) != 1
                or RUN_AGENT_PY_PATCH_DESCRIPTION_RE.fullmatch(descriptions[0]) is None
            ):
                return _patch_error(
                    "PatchScopeError",
                    "The patch must contain exactly one Update File operation for run.agent.py without add, delete, or move.",
                    canonical_path,
                    script_fingerprint=current_fingerprint,
                )

            try:
                result = apply_patch_in_memory(str(patch), {"run.agent.py": current_content})
            except Exception:  # noqa: BLE001 - 0.4.0 emits an empty RuntimeError for context conflicts.
                return _patch_error(
                    "PatchConflict",
                    "Patch context does not match the current run.agent.py.",
                    canonical_path,
                    script_fingerprint=current_fingerprint,
                )
            if (
                result.added
                or result.deleted
                or result.modified != ["run.agent.py"]
                or set(result.files) != {"run.agent.py"}
            ):
                return _patch_error(
                    "PatchResultError",
                    "The patch engine returned changes outside run.agent.py.",
                    canonical_path,
                    script_fingerprint=current_fingerprint,
                )

            updated_content = result.files["run.agent.py"]
            try:
                compile(updated_content, str(run_path), "exec")
            except SyntaxError as exc:
                return _patch_error(
                    "InvalidPython",
                    exc.msg,
                    canonical_path,
                    script_fingerprint=current_fingerprint,
                    line=exc.lineno,
                    offset=exc.offset,
                )

            temp_path: Path | None = None
            try:
                with NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    newline="",
                    dir=page_dir,
                    prefix=".run.agent.py.",
                    suffix=".tmp",
                    delete=False,
                ) as temp_file:
                    temp_file.write(updated_content)
                    temp_file.flush()
                    os.fsync(temp_file.fileno())
                    temp_path = Path(temp_file.name)
                os.chmod(temp_path, run_path.stat().st_mode)
                os.replace(temp_path, run_path)
                temp_path = None
            except OSError as exc:
                return _patch_error(
                    "PatchCommitError",
                    str(exc),
                    canonical_path,
                    script_fingerprint=current_fingerprint,
                )
            finally:
                if temp_path is not None:
                    temp_path.unlink(missing_ok=True)

            updated_fingerprint = _fingerprint_text(updated_content)
            return json.dumps(
                {
                    "ok": True,
                    "applied": True,
                    "canonical_path": canonical_path,
                    "path": run_path.relative_to(self.root).as_posix(),
                    "script_fingerprint": updated_fingerprint,
                    "bytes_before": len(current_content.encode("utf-8")),
                    "bytes_after": len(updated_content.encode("utf-8")),
                },
                ensure_ascii=False,
                sort_keys=True,
            )

    def read_collection_guide(self, current_path: str) -> dict:
        page_dir, _, error = self._page_dir(current_path)
        if error:
            raise ValueError(error)
        path = page_dir / "COLLECT.md"
        if path.is_symlink() or path.resolve().parent != page_dir.resolve():
            raise ValueError("COLLECT.md must stay in the page directory")
        content = path.read_text(encoding="utf-8")
        metadata, _, body = content.partition("\n")
        required = json.loads(metadata).get("required_aliases")
        if not isinstance(required, list) or not required or any(
            not isinstance(alias, str) or not re.fullmatch(r"0|[1-9][0-9]*", alias) for alias in required
        ) or len(set(required)) != len(required) or not body.strip():
            raise ValueError("COLLECT.md needs JSON required_aliases followed by an English guide")
        return {"content": content, "required_aliases": required,
                "fingerprint": hashlib.sha256(content.encode()).hexdigest()}

    def write_collection_guide(self, current_path: str, content: str) -> dict:
        page_dir, _, error = self._page_dir(current_path)
        if error:
            raise ValueError(error)
        metadata, _, body = content.partition("\n")
        aliases = json.loads(metadata).get("required_aliases")
        if not isinstance(aliases, list) or not aliases or not body.strip() or any(
            not isinstance(alias, str) or not re.fullmatch(r"0|[1-9][0-9]*", alias) for alias in aliases
        ) or len(set(aliases)) != len(aliases):
            raise ValueError('First line must be {"required_aliases":["1"]}, followed by an English guide')
        path = page_dir / "COLLECT.md"
        if path.is_symlink() or path.resolve().parent != page_dir.resolve():
            raise ValueError("COLLECT.md must stay in the page directory")
        with self._run_patch_lock:
            temporary = None
            try:
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=page_dir, delete=False) as handle:
                    temporary = handle.name
                    handle.write(content)
                os.replace(temporary, path)
            finally:
                if temporary and os.path.exists(temporary):
                    os.unlink(temporary)
        return self.read_collection_guide(current_path)

    def sniff_run(self, current_path: str, view: ReadOnlyPageView) -> str:
        result = self.run_page_script(current_path, view)
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    def run_page_script(self, current_path: str, view: ReadOnlyPageView) -> dict[str, Any]:
        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return _script_error("page_path_error", error, canonical_path=canonical_path)
        run_path = page_dir / "run.agent.py"
        if not run_path.is_file():
            return _script_error("missing_run_agent_py", "run.agent.py was not found.", canonical_path=canonical_path)

        try:
            if not isinstance(view, ReadOnlyPageView):
                raise TypeError("run(view) requires a frozen ReadOnlyPageView; XML scripts are not supported")
            view.require_aliases(self.read_collection_guide(current_path)["required_aliases"])
            module = _load_run_module(run_path)
            try:
                run = module.run
            except AttributeError:
                return _script_error(
                    "missing_run_function",
                    "run.agent.py must define run(view).",
                    canonical_path=canonical_path,
                )
            if not callable(run):
                return _script_error("missing_run_function", "run.agent.py must define run(view).", canonical_path=canonical_path)
            if list(inspect.signature(run).parameters) != ["view"]:
                raise TypeError("run.agent.py must define run(view); migrate the old XML script")
            output = run(view)
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

    def is_merchant_page(self, current_path: str) -> bool:
        return self.page_policy(current_path).role == "merchant"

    def page_policy(self, current_path: str) -> PagePolicy:
        parts, error = normalize_page_path(current_path)
        if error or not parts:
            return PagePolicy()
        return self._page_policies.get("/".join(parts), PagePolicy())

    def _query_doc(
        self,
        current_path: str,
        filename: str,
        block_name: str,
    ) -> str:
        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            raise ValueError(error)
        doc = page_dir / filename
        if not doc.is_file():
            raise FileNotFoundError(f"{filename} was not found for page {canonical_path!r}.")
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


class CollectorTrainingRegistry:
    """Track collector results by concrete page visit, never by page class."""

    def __init__(self) -> None:
        self._records: dict[str, CollectorTrainingRecord] = {}
        self._lock = Lock()

    def register_visit(self, visit_id: str, current_path: str) -> CollectorTrainingRecord:
        visit_id = str(visit_id or "").strip()
        canonical_path = self._canonical_path(current_path)
        if not visit_id:
            raise ValueError("visit_id is required.")
        if not canonical_path:
            raise ValueError("current_path is required.")
        with self._lock:
            record = self._records.get(visit_id)
            if record is not None and record.page_path != canonical_path:
                raise ValueError("visit_id is already registered for another page path.")
            if record is None:
                record = CollectorTrainingRecord(visit_id=visit_id, page_path=canonical_path)
                self._records[visit_id] = record
            return _copy_training_record(record)

    def mark_trained(
        self,
        visit_id: str,
        current_path: str,
        *,
        merchant_profile: Mapping[str, Any] | None = None,
        script_fingerprint: str = "",
        restoration: Mapping[str, Any] | None = None,
        contract_fingerprint: str = "",
    ) -> None:
        self.register_visit(visit_id, current_path)
        with self._lock:
            record = self._records[visit_id]
            record.trained = True
            record.merchant_profile = dict(merchant_profile or {})
            record.script_fingerprint = str(script_fingerprint or "")
            record.restoration = dict(restoration or {})
            record.contract_fingerprint = contract_fingerprint

    def mark_untrained(
        self,
        visit_id: str,
        current_path: str,
        *,
        merchant_profile: Mapping[str, Any] | None = None,
        script_fingerprint: str = "",
        restoration: Mapping[str, Any] | None = None,
    ) -> None:
        self.register_visit(visit_id, current_path)
        with self._lock:
            record = self._records[visit_id]
            record.trained = False
            record.merchant_profile = dict(merchant_profile or {})
            record.script_fingerprint = str(script_fingerprint or "")
            record.restoration = dict(restoration or {})

    def is_trained(self, visit_id: str, current_path: str | None = None) -> bool:
        record = self.record_for(visit_id)
        if record is None or not record.trained:
            return False
        return current_path is None or record.page_path == self._canonical_path(current_path)

    def record_for(self, visit_id: str) -> CollectorTrainingRecord | None:
        with self._lock:
            record = self._records.get(str(visit_id or ""))
            return _copy_training_record(record) if record is not None else None

    def _canonical_path(self, current_path: str) -> str:
        parts, error = normalize_page_path(current_path)
        if error or not parts:
            return ""
        return "/".join(parts)


def _copy_training_record(record: CollectorTrainingRecord) -> CollectorTrainingRecord:
    return CollectorTrainingRecord(
        visit_id=record.visit_id,
        page_path=record.page_path,
        trained=record.trained,
        merchant_profile=dict(record.merchant_profile),
        script_fingerprint=record.script_fingerprint,
        restoration=dict(record.restoration),
        contract_fingerprint=record.contract_fingerprint,
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


def eval_collection(r1: Any, r2: Any) -> dict[str, Any]:
    expected = _coerce_json(r1)
    actual = _coerce_json(r2)
    if isinstance(actual, Mapping) and actual.get("ok") is True and "result" in actual:
        actual = actual["result"]
    if isinstance(actual, Mapping) and actual.get("ok") is False:
        return {"score": 0, "errors": ["result"], "missing": []}

    errors: list[str] = []
    missing: list[str] = []
    matched = _compare_json(expected, actual, "result", errors, missing)
    failures = len(errors) + len(missing)
    total = matched + failures
    score = 100 if total == 0 else round((matched / total) * 100)
    return {"score": score, "errors": errors, "missing": missing}


def _load_run_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_page_run_{abs(hash(path))}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load run.agent.py: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assert_json_object(value: Any) -> None:
    if not isinstance(value, dict):
        raise TypeError("run.agent.py must return a dict.")
    json.dumps(value, ensure_ascii=False)


def _script_error(error_type: str, error: str, *, canonical_path: str) -> dict[str, Any]:
    return {"ok": False, "canonical_path": canonical_path, "error_type": error_type, "error": error, "traceback": ""}


def _patch_error(
    error_type: str,
    error: str,
    canonical_path: str,
    *,
    script_fingerprint: str = "",
    **details: Any,
) -> str:
    return json.dumps(
        {
            "ok": False,
            "applied": False,
            "canonical_path": canonical_path,
            "error_type": error_type,
            "error": error,
            "script_fingerprint": script_fingerprint,
            **details,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _fingerprint_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _coerce_json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _compare_json(expected: Any, actual: Any, path: str, errors: list[str], missing: list[str]) -> int:
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            errors.append(path)
            return 0
        matched = 0
        for key in sorted(expected):
            child_path = f"{path}.{key}"
            if key not in actual:
                missing.append(child_path)
            else:
                matched += _compare_json(expected[key], actual[key], child_path, errors, missing)
        for key in sorted(set(actual) - set(expected)):
            errors.append(f"{path}.{key}")
        return matched
    if isinstance(expected, list):
        if not isinstance(actual, list):
            errors.append(path)
            return 0
        matched = 0
        common_length = min(len(expected), len(actual))
        for index in range(common_length):
            matched += _compare_json(expected[index], actual[index], f"{path}[{index}]", errors, missing)
        for index in range(common_length, len(expected)):
            missing.append(f"{path}[{index}]")
        for index in range(common_length, len(actual)):
            errors.append(f"{path}[{index}]")
        return matched
    if expected != actual:
        errors.append(path)
        return 0
    return 1


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
