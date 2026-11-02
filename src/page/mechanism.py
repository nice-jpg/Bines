"""Page mechanism loading, page-local run.agent.py execution, and evaluation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import traceback
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Final

from codex_apply_patch import InMemoryPatchResult, apply_patch_in_memory, parse_patch

from src.common.utils.hashing import fingerprint_text
from src.common.utils.json import JsonObject, JsonValue, load_json
from src.common.utils.xml import escape_attr, escape_text
from src.device import ReadOnlyPageView
from src.page.collection_contract import CollectionContract
from src.page.collection_types import CollectionGuide
from src.page.documents import PageDocumentStore
from src.page.paths import normalize_page_path
from src.page.script_types import ScriptFailure, ScriptResult, ScriptSourceResult
from src.page.scripts import (
    MissingRunFunction,
    PageScriptExecutor,
)

PAGE_MECHANISM_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "prompts" / "page_mechanism"
RUN_AGENT_PY_PATCH_DESCRIPTION_RE: Final[re.Pattern[str]] = re.compile(
    r"UpdateFile: run\.agent\.py \([1-9]\d* chunks\)"
)


class PageMechanism:
    """Resolve page mechanism files under ``prompts/page_mechanism``."""

    def __init__(self) -> None:
        self.root: Path = PAGE_MECHANISM_DIR.resolve()
        self._states: dict[str, JsonObject] = {}
        self._run_patch_lock: Lock = Lock()
        self._documents: PageDocumentStore = PageDocumentStore(self._run_patch_lock)
        self._scripts: PageScriptExecutor = PageScriptExecutor()

    def query_page_doc(self, current_path: str) -> str:
        return self._query_doc(current_path, "PAGE.md", "page_doc")

    def manual_path(self, current_path: str) -> Path:
        canonical: str = self.validate_page_manual(current_path)
        target: Path = (self.root / canonical / "PAGE.md").resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("Manual escapes page root")
        return target

    def update_page_notes(self, current_path: str, content: str) -> bool:
        return self._documents.update_section(
            self.manual_path(current_path), "operation-notes", "## Operation Observations\n" + content
        )

    def collection_contract(self, current_path: str) -> CollectionContract | None:
        content: str | None = self._documents.read_section(
            self.manual_path(current_path), "collection-contract"
        )
        if content is None:
            return None
        match: re.Match[str] | None = re.search(r"```json\s*\n(.*?)\n```", content, re.DOTALL)
        if match is None:
            raise ValueError("Collection Requirements must contain a JSON block")
        return CollectionContract.from_dict(json.loads(match.group(1)))

    def save_collection_contract(self, current_path: str, contract: CollectionContract) -> bool:
        content: str = (
            "## Collection Requirements\n```json\n" + json.dumps(contract.as_dict(), sort_keys=True) + "\n```"
        )
        return self._documents.update_section(self.manual_path(current_path), "collection-contract", content)

    def query_task_doc(self, current_path: str) -> str:
        return self._query_doc(current_path, "task.md", "task_doc")

    def read_schema(self, current_path: str, heading: str) -> JsonObject:
        """Read a named PAGE.md schema without relying on model-provided JSON."""
        content = self.manual_path(current_path).read_text(encoding="utf-8")
        section = re.search(r"^## " + re.escape(heading) + r"\s*\n(.*?)(?=^## |\Z)", content, re.M | re.S)
        block = re.search(r"```json\s*\n(.*?)\n```", section.group(1), re.S) if section else None
        if block is None:
            raise ValueError(f"PAGE.md must define {heading} as a JSON object")
        value = load_json(block.group(1))
        if not isinstance(value, dict):
            raise ValueError(f"{heading} must be a JSON object")
        return value

    def load_manual_bundle(self, current_path: str) -> str:
        """Strictly load PAGE.md followed by task.md for one canonical page."""

        canonical_path: str = self.validate_page_manual(current_path)
        return "\n".join(
            [
                f'<current_page_manual canonical_path="{escape_attr(canonical_path)}">',
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
        missing: list[str] = [name for name in ("PAGE.md", "task.md") if not (page_dir / name).is_file()]
        if missing:
            raise FileNotFoundError(
                f"Page {canonical_path!r} is missing required manual files: {', '.join(missing)}"
            )
        return canonical_path

    def read_run_agent_py(self, current_path: str) -> str:
        snapshot: ScriptSourceResult = self.read_run_agent_py_snapshot(current_path)
        if snapshot["ok"] is True:
            return snapshot["content"]
        return self._error_block(
            "run_agent_py_error",
            current_path,
            snapshot["canonical_path"],
            snapshot["error"],
        )

    def read_run_agent_py_snapshot(self, current_path: str) -> ScriptSourceResult:
        """Read run.agent.py with a structured error contract for internal tools."""

        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return {
                "ok": False,
                "canonical_path": canonical_path,
                "error_type": "RunAgentPyPathError",
                "error": error,
            }
        run_path: Path = (page_dir / "run.agent.py").resolve()
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
        run_path: Path = (page_dir / "run.agent.py").resolve()
        if not self._is_relative_to(run_path):
            return self._error_block(
                "run_agent_py_error",
                current_path,
                canonical_path,
                "run.agent.py path escapes page root.",
            )
        run_path.write_text(str(content), encoding="utf-8")
        return json.dumps(
            {
                "ok": True,
                "canonical_path": canonical_path,
                "path": run_path.relative_to(self.root).as_posix(),
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def apply_run_agent_py_patch(self, current_path: str, patch: str) -> str:
        """Apply one context patch to run.agent.py after in-memory validation."""

        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return _patch_error("PatchScopeError", error, canonical_path)
        run_path: Path = (page_dir / "run.agent.py").resolve()
        if not self._is_relative_to(run_path) or not run_path.is_file():
            return _patch_error(
                "PatchScopeError",
                "The current page must contain an existing run.agent.py.",
                canonical_path,
            )

        with self._run_patch_lock:
            current_content: str = run_path.read_text(encoding="utf-8")
            current_fingerprint: str = fingerprint_text(current_content)
            try:
                descriptions: list[str] = parse_patch(str(patch))
            except Exception as exc:  # noqa: BLE001 - extension exposes parse failures as RuntimeError.
                return _patch_error(
                    "InvalidPatchFormat",
                    str(exc).strip() or "The patch does not follow the Codex patch format.",
                    canonical_path,
                    script_fingerprint=current_fingerprint,
                )
            if len(descriptions) != 1 or RUN_AGENT_PY_PATCH_DESCRIPTION_RE.fullmatch(descriptions[0]) is None:
                return _patch_error(
                    "PatchScopeError",
                    "The patch must contain exactly one Update File operation for run.agent.py without add, delete, or move.",
                    canonical_path,
                    script_fingerprint=current_fingerprint,
                )

            try:
                result: InMemoryPatchResult = apply_patch_in_memory(
                    str(patch), {"run.agent.py": current_content}
                )
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

            updated_content: str = result.files["run.agent.py"]
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

            updated_fingerprint: str = fingerprint_text(updated_content)
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

    def read_collection_guide(self, current_path: str) -> CollectionGuide:
        page_dir, _, error = self._page_dir(current_path)
        if error:
            raise ValueError(error)
        path: Path = page_dir / "COLLECT.md"
        if path.is_symlink() or path.resolve().parent != page_dir.resolve():
            raise ValueError("COLLECT.md must stay in the page directory")
        content: str = path.read_text(encoding="utf-8")
        required: list[str] = _guide_aliases(content)
        return {
            "content": content,
            "required_aliases": required,
            "fingerprint": hashlib.sha256(content.encode()).hexdigest(),
        }

    def write_collection_guide(self, current_path: str, content: str) -> CollectionGuide:
        page_dir, _, error = self._page_dir(current_path)
        if error:
            raise ValueError(error)
        _guide_aliases(content)
        path: Path = page_dir / "COLLECT.md"
        if path.is_symlink() or path.resolve().parent != page_dir.resolve():
            raise ValueError("COLLECT.md must stay in the page directory")
        with self._run_patch_lock:
            temporary: str | None = None
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
        result: ScriptResult = self.run_page_script(current_path, view)
        return json.dumps(result, ensure_ascii=False, sort_keys=True)

    def run_page_script(self, current_path: str, view: object) -> ScriptResult:
        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            return _script_error("page_path_error", error, canonical_path=canonical_path)
        run_path: Path = page_dir / "run.agent.py"
        if not run_path.is_file():
            return _script_error(
                "missing_run_agent_py", "run.agent.py was not found.", canonical_path=canonical_path
            )

        try:
            if not isinstance(view, ReadOnlyPageView):
                raise TypeError("run(view) requires a frozen ReadOnlyPageView; XML scripts are not supported")
            view.require_aliases(self.read_collection_guide(current_path)["required_aliases"])
            output: JsonObject = self._scripts.execute(run_path, view)
        except MissingRunFunction as exc:
            return _script_error("missing_run_function", str(exc), canonical_path=canonical_path)
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

    def state_for(self, current_path: str) -> JsonObject | None:
        _, canonical_path, error = self._page_dir(current_path)
        if error:
            return None
        state: JsonObject | None = self._states.get(canonical_path)
        return dict(state) if state is not None else None

    def _query_doc(
        self,
        current_path: str,
        filename: str,
        block_name: str,
    ) -> str:
        page_dir, canonical_path, error = self._page_dir(current_path)
        if error:
            raise ValueError(error)
        doc: Path = page_dir / filename
        if not doc.is_file():
            raise FileNotFoundError(f"{filename} was not found for page {canonical_path!r}.")
        relative_path: str = doc.relative_to(self.root).as_posix()
        return "\n".join(
            [
                f'<{block_name} canonical_path="{escape_attr(canonical_path)}">',
                f'<file path="{escape_attr(relative_path)}">',
                doc.read_text(encoding="utf-8"),
                "</file>",
                f"</{block_name}>",
            ]
        )

    def _page_dir(self, current_path: str) -> tuple[Path, str, str | None]:
        parts, error = normalize_page_path(current_path)
        canonical_path: str = "/".join(parts)
        if error:
            return self.root, canonical_path, error
        if not parts:
            return self.root, canonical_path, "No page path was provided."
        page_dir: Path = self.root.joinpath(*parts).resolve()
        if not self._is_relative_to(page_dir):
            return self.root, canonical_path, "Page path escapes page mechanism root."
        return page_dir, canonical_path, None

    def _error_block(self, name: str, raw_path: str, attempted_path: str, message: str) -> str:
        lines: list[str] = [
            f"<{name}>",
            f"<message>{escape_text(message)}</message>",
            f"<input_path>{escape_text(raw_path)}</input_path>",
            f"<attempted_path>{escape_text(attempted_path)}</attempted_path>",
            "<available_paths>",
        ]
        for path in self.available_paths():
            lines.append(f"<path>{escape_text(path)}</path>")
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


def _script_error(error_type: str, error: str, *, canonical_path: str) -> ScriptFailure:
    return {
        "ok": False,
        "canonical_path": canonical_path,
        "error_type": error_type,
        "error": error,
        "traceback": "",
    }


def _patch_error(
    error_type: str,
    error: str,
    canonical_path: str,
    *,
    script_fingerprint: str = "",
    **details: JsonValue,
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


def _guide_aliases(content: str) -> list[str]:
    metadata, _, body = content.partition("\n")
    parsed: JsonValue = load_json(metadata)
    if not isinstance(parsed, dict):
        raise ValueError("COLLECT.md metadata must be a JSON object")
    required: JsonValue = parsed.get("required_aliases")
    if not isinstance(required, list) or not required or not body.strip():
        raise ValueError("COLLECT.md needs JSON required_aliases followed by an English guide")
    aliases: list[str] = []
    for alias in required:
        if not isinstance(alias, str) or re.fullmatch(r"0|[1-9][0-9]*", alias) is None:
            raise ValueError("Collection aliases must be non-negative integer strings")
        aliases.append(alias)
    if len(set(aliases)) != len(aliases):
        raise ValueError("Collection aliases must be unique")
    return aliases
