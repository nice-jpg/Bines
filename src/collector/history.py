"""Atomic visual evidence isolated by probe run, page and visit."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock


class CollectorHistory:
    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).resolve().parents[2] / "workspace" / "collector_history").resolve()
        self._lock = RLock()

    def _directory(self, path, run_id):
        if not run_id or Path(run_id).name != run_id or run_id in {".", ".."}:
            raise ValueError("Invalid probe run ID")
        run_root = self.root / "runs" / run_id
        if run_root.resolve() != run_root:
            raise ValueError("History run directory must not be a symlink")
        directory = (run_root / path).resolve()
        if directory == run_root or not directory.is_relative_to(run_root):
            raise ValueError("Invalid history page path")
        return directory

    def load(self, path, *, run_id):
        with self._lock:
            records = {}
            directory = self._directory(path, run_id)
            for file in sorted(directory.glob("*/*/record.json")):
                if not file.resolve().is_relative_to(directory):
                    raise ValueError("History symlinks must stay inside the run's page directory")
                record = json.loads(file.read_text(encoding="utf-8"))
                if record.get("format_version") != 2 or record.get("run_id") != run_id or not record.get("image") or record.get("baseline") is None:
                    raise ValueError(f"Unsupported or incomplete visual history: {file}")
                records[record["case_id"]] = record
            return list(records.values())

    def commit(self, path, visit_id, candidates, historical, *, run_id):
        """Retain every screenshot-backed baseline, including cases the script fails."""
        with self._lock:
            directory = self._directory(path, run_id)
            if any(record.get("run_id") != run_id for record in [*historical, *candidates]):
                raise ValueError("Cannot archive a case from another probe run")
            for record in historical:
                self._write(directory, record["page_visit_id"], record)
            for candidate in candidates:
                record = dict(candidate, format_version=2, page_visit_id=visit_id)
                self._write(directory, visit_id, record)
            return [case["case_id"] for case in candidates]

    def _write(self, page_directory, visit_id, record):
        if not visit_id or Path(visit_id).name != visit_id or visit_id in {".", ".."}:
            raise ValueError("Invalid visit ID")
        case_id = record["case_id"]
        if not case_id or Path(case_id).name != case_id or case_id in {".", ".."}:
            raise ValueError("Invalid case ID")
        directory = (page_directory / visit_id / case_id).resolve()
        if not directory.is_relative_to(page_directory):
            raise ValueError("History destination escapes the run's page directory")
        directory.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory, delete=False) as handle:
                temporary = handle.name
                json.dump(record, handle, ensure_ascii=False, sort_keys=True)
            os.replace(temporary, directory / "record.json")
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
