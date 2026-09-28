"""Best-effort offline evidence sampling, never an online evaluator."""

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import random
from uuid import uuid4

from src.tools.screenshot import capture_png


def sample_collection(hub, root, page_path, visit_id, snapshot, collection, admission, contract, action, script_fingerprint):
    if hub is None or random.random() >= 0.05:
        return
    try:
        root = Path(root).resolve()
        directory = (root / page_path / visit_id / uuid4().hex).resolve()
        if not directory.is_relative_to(root):
            raise ValueError("Invalid audit path")
        image = capture_png(hub)
        directory.mkdir(parents=True)
        (directory / "screen.png").write_bytes(image)
        record = {"captured_at": datetime.now(timezone.utc).isoformat(), "snapshot": snapshot,
                  "collection": collection, "admission": admission, "contract": contract.as_dict() if contract else None,
                  "action": action, "script_fingerprint": script_fingerprint,
                  "page_path": page_path, "page_visit_id": visit_id}
        temporary = directory / "record.tmp"
        temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        temporary.replace(directory / "record.json")
    except Exception:
        logging.getLogger(__name__).warning("Collection audit failed", exc_info=True)
