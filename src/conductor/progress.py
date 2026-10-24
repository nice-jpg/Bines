"""Read-only task progress, never completion authorization."""

from src.common.utils.json import JsonObject
from src.conductor.state import ConductorFields


def summarize_probe_progress(state: ConductorFields) -> JsonObject:
    visited: set[str] = {
        fact["path"] for fact in state["probe"].get("visit_history", ()) if fact["event"] == "entered"
    }
    targets: tuple[str, ...] = state["probe"].get("target_leaf_paths", ())
    return {
        "targets": list(targets),
        "visited": sorted(visited),
        "completed_merchants": {
            path: list(names) for path, names in state["probe"].get("completed_merchants", {}).items()
        },
        "not_yet_visited": [path for path in targets if path not in visited],
        "collection_evidence": [
            {
                "page_path": summary["page_path"],
                "merchant_profile": summary["merchant_profile"],
                "collected_items": summary["collected_items"],
                "completion": "unknown",
            }
            for summary in state["probe"].get("visit_summaries", {}).values()
        ],
    }
