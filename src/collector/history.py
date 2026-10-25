"""Atomic visual evidence isolated by probe run, page and visit."""

import json
import os
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Final
from uuid import uuid4

from pydantic import TypeAdapter

from src.collector.validation import ValidationCase
from src.common.paths import WORKSPACE_ROOT

COLLECTOR_HISTORY_ROOT: Final[Path] = WORKSPACE_ROOT / "collector_history"


class CollectorHistory:
    def __init__(self) -> None:
        self.root: Path = COLLECTOR_HISTORY_ROOT.resolve()
        self._lock: RLock = RLock()

    def _directory(self, path: str, run_id: str) -> Path:
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("Invalid probe run ID")
        run_root: Path = self.root / "runs" / run_id
        if run_root.resolve() != run_root:
            raise ValueError("History run directory must not be a symlink")
        directory: Path = (run_root / path).resolve()
        if directory == run_root or not directory.is_relative_to(run_root):
            raise ValueError("Invalid history page path")
        return directory

    def load(self, path: str, *, run_id: str) -> list[ValidationCase]:
        with self._lock:
            records: dict[str, ValidationCase] = {}
            directory: Path = self._directory(path, run_id)
            for file in sorted(directory.glob("*/*/record.json")):
                if not file.resolve().is_relative_to(directory):
                    raise ValueError("History symlinks must stay inside the run's page directory")
                record: ValidationCase = TypeAdapter(ValidationCase).validate_json(
                    file.read_text(encoding="utf-8"), strict=True
                )
                if (
                    record.get("format_version") != 2
                    or record.get("run_id") != run_id
                    or not record.get("image")
                    or record.get("baseline") is None
                ):
                    raise ValueError(f"Unsupported or incomplete visual history: {file}")
                records[record["case_id"]] = record
            return list(records.values())

    def commit(
        self,
        path: str,
        visit_id: str,
        candidates: Sequence[ValidationCase],
        historical: Sequence[ValidationCase],
        *,
        run_id: str,
    ) -> list[str]:
        """Retain every screenshot-backed baseline, including cases the script fails."""
        with self._lock:
            directory: Path = self._directory(path, run_id)
            if any(record.get("run_id") != run_id for record in [*historical, *candidates]):
                raise ValueError("Cannot archive a case from another probe run")
            for record in historical:
                self._write(directory, record["page_visit_id"], record)
            for candidate in candidates:
                record: ValidationCase = {**candidate, "format_version": 2, "page_visit_id": visit_id}
                self._write(directory, visit_id, record)
            return [case["case_id"] for case in candidates]

    def retire_other_guides(self, path: str, guide_fingerprint: str, *, run_id: str) -> None:
        """Remove obsolete annotation cases from regression while retaining their audit records."""
        with self._lock:
            directory: Path = self._directory(path, run_id)
            archive: Path = directory / "invalidated" / uuid4().hex
            for record in self.load(path, run_id=run_id):
                if record["guide_fingerprint"] == guide_fingerprint:
                    continue
                source: Path = directory / record["page_visit_id"] / record["case_id"] / "record.json"
                if not source.resolve().is_relative_to(directory):
                    raise ValueError("History source escapes the run's page directory")
                destination: Path = archive / record["page_visit_id"] / record["case_id"] / "record.json"
                if not destination.resolve().is_relative_to(archive):
                    raise ValueError("History archive escapes its directory")
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)

    def _write(self, page_directory: Path, visit_id: str, record: ValidationCase) -> None:
        if not visit_id or Path(visit_id).name != visit_id or visit_id in {".", ".."}:
            raise ValueError("Invalid visit ID")
        case_id: str = record["case_id"]
        if not case_id or Path(case_id).name != case_id or case_id in {".", ".."}:
            raise ValueError("Invalid case ID")
        directory: Path = (page_directory / visit_id / case_id).resolve()
        if not directory.is_relative_to(page_directory):
            raise ValueError("History destination escapes the run's page directory")
        directory.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as handle:
                temporary = handle.name
                json.dump(record, handle, ensure_ascii=False, sort_keys=True)
            os.replace(temporary, directory / "record.json")
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
