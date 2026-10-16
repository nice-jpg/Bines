"""Best-effort offline evidence sampling, never an online evaluator."""

import json
import logging
import random
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.device import PageSnapshot
from src.page.collection_contract import CollectionContract, CollectionContractData
from src.page.script_types import ScriptResult
from src.serialization.json import JsonValue
from src.tools.collection.types import AdmissionAudit
from src.tools.device.screenshots import ScreenshotSource, capture_png


def sample_collection(
    hub: ScreenshotSource | None,
    root: str | Path,
    page_path: str,
    visit_id: str,
    snapshot: PageSnapshot,
    collection: ScriptResult,
    admission: AdmissionAudit,
    contract: CollectionContract | None,
    action: Mapping[str, JsonValue],
    script_fingerprint: str,
) -> None:
    if hub is None or random.random() >= 0.05:
        return
    try:
        root = Path(root).resolve()
        directory: Path = (root / page_path / visit_id / uuid4().hex).resolve()
        if not directory.is_relative_to(root):
            raise ValueError("Invalid audit path")
        image: bytes = capture_png(hub)
        directory.mkdir(parents=True)
        (directory / "screen.png").write_bytes(image)
        record: dict[
            str,
            ScriptResult
            | str
            | PageSnapshot
            | AdmissionAudit
            | CollectionContractData
            | Mapping[str, JsonValue]
            | None,
        ] = {
            "captured_at": datetime.now(UTC).isoformat(),
            "snapshot": snapshot,
            "collection": collection,
            "admission": admission,
            "contract": contract.as_dict() if contract else None,
            "action": action,
            "script_fingerprint": script_fingerprint,
            "page_path": page_path,
            "page_visit_id": visit_id,
        }
        temporary: Path = directory / "record.tmp"
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(directory / "record.json")
    except Exception:
        logging.getLogger(__name__).warning("Collection audit failed", exc_info=True)
