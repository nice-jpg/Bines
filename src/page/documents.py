"""Atomic reads and updates of named sections in page manuals."""

import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Lock


@dataclass(frozen=True)
class ManualSection:
    original: str
    start: str
    end: str
    content: str | None


class PageDocumentStore:
    def __init__(self, lock: Lock) -> None:
        self._lock: Lock = lock

    def _read(self, path: Path, section: str) -> ManualSection:
        original: str = path.read_text(encoding="utf-8")
        start: str = f"<!-- {section}:begin -->"
        end: str = f"<!-- {section}:end -->"
        if original.count(start) != original.count(end) or original.count(start) > 1:
            raise ValueError("Invalid manual section markers")
        if start in original and original.index(start) >= original.index(end):
            raise ValueError("Reversed manual section markers")
        content: str | None = (
            original.split(start, 1)[1].split(end, 1)[0].strip() if start in original else None
        )
        return ManualSection(original, start, end, content)

    def read_section(self, path: Path, section: str) -> str | None:
        with self._lock:
            return self._read(path, section).content

    def update_section(self, path: Path, section: str, content: str) -> bool:
        with self._lock:
            current: ManualSection = self._read(path, section)
            if "<!--" in content or "-->" in content:
                raise ValueError("Section content cannot contain control markers")
            if current.content == content.strip():
                return False
            block: str = f"{current.start}\n{content.strip()}\n{current.end}"
            updated: str = (
                current.original.split(current.start, 1)[0]
                + block
                + current.original.split(current.end, 1)[1]
                if current.content is not None
                else current.original.rstrip() + "\n\n" + block + "\n"
            )
            temporary: str | None = None
            try:
                with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                    temporary = handle.name
                    handle.write(updated)
                os.replace(temporary, path)
            finally:
                if temporary is not None and os.path.exists(temporary):
                    os.unlink(temporary)
            return True
