"""Atomic, visit-scoped visual evidence retained across collector runs."""

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock


class CollectorHistory:
    def __init__(self, root=None):
        self.root = Path(root or Path(__file__).resolve().parents[2] / "workspace" / "collector_history").resolve()
        self._lock = RLock()

    def _directory(self, path):
        directory = (self.root / path).resolve()
        if directory == self.root or not directory.is_relative_to(self.root):
            raise ValueError("Invalid history page path")
        return directory

    def load(self, path):
        with self._lock:
            records = {}
            for file in sorted(self._directory(path).glob("**/record.json"), key=lambda p: (len(p.parts), str(p))):
                if not file.resolve().is_relative_to(self.root):
                    raise ValueError("History symlinks must stay inside history root")
                record = json.loads(file.read_text(encoding="utf-8"))
                if record.get("format_version") != 1 or not record.get("image") or record.get("baseline") is None:
                    raise ValueError(f"Unsupported or incomplete visual history: {file}")
                records[record["case_id"]] = record
            return list(records.values())

    def commit(self, path, visit_id, candidates, historical):
        """Retain every screenshot-backed baseline, including cases the script fails."""
        with self._lock:
            for record in historical:
                self._write(path, record["page_visit_id"], record)
            for candidate in candidates:
                record = dict(candidate, format_version=1, page_visit_id=visit_id)
                self._write(path, visit_id, record)
            return [case["case_id"] for case in candidates]

    def _write(self, path, visit_id, record):
        if not visit_id or Path(visit_id).name != visit_id or visit_id in {".", ".."}:
            raise ValueError("Invalid visit ID")
        case_id = record["case_id"]
        if not case_id or Path(case_id).name != case_id or case_id in {".", ".."}:
            raise ValueError("Invalid case ID")
        directory = (self._directory(path) / visit_id / case_id).resolve()
        if not directory.is_relative_to(self.root):
            raise ValueError("History destination escapes its root")
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
