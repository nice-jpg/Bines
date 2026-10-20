"""One-way migration of pre-namespace conductor checkpoints."""

from src.common.utils.validation import is_string_mapping


def migrate_conductor_checkpoint(value: dict[str, object]) -> dict[str, object]:
    """Translate recognized legacy fields; reject ambiguous visits and evidence."""
    if "general" in value or "merchant" in value or "probe" in value:
        if any(key.startswith(("page_context_", "probe_", "collection_")) for key in value):
            raise ValueError("Checkpoint mixes legacy fields and namespaced state")
        return value
    if (
        not any(key.startswith(("page_context_", "probe_", "collection_")) for key in value)
        and "page_visit" not in value
    ):
        return value
    visit: object = value.get("page_visit")
    path: object = value.get("page_context_path", "")
    visit_id: object = value.get("page_visit_id", "")
    if is_string_mapping(visit):
        path = path or visit.get("path", "")
        visit_id = visit_id or visit.get("visit_id", "")
    elif path:
        raise ValueError("Legacy checkpoint has a page path without a strategy identity; start a new probe")
    names: dict[str, str] = {
        "general_operation": "general",
        "structured_office": "merchant",
        "general": "general",
        "merchant": "merchant",
    }
    general: dict[str, object] = {
        "active": value.get("page_context_active", False),
        "app_root": value.get("page_context_app_root", ""),
        "visit": None,
        "pending": None,
    }
    merchant: dict[str, object] = {}
    if is_string_mapping(visit):
        kind: object = visit.get("kind")
        if (
            not isinstance(kind, str)
            or kind not in names
            or not isinstance(path, str)
            or not path
            or not isinstance(visit_id, str)
            or not visit_id
        ):
            raise ValueError("Legacy checkpoint cannot establish a valid page visit")
        general["visit"] = {"visit_id": visit_id, "path": path, "strategy": names[kind]}
        if names[kind] == "merchant":
            if visit.get("phase") not in {"awaiting_training", "ready", "traversing", "completed"}:
                raise ValueError("Legacy merchant checkpoint has no valid phase")
            evidence_id: object = value.get("collection_page_visit_id", "")
            if evidence_id and evidence_id != visit_id:
                raise ValueError("Legacy collection evidence belongs to another visit")
            evidence: dict[str, object] = {
                "initialized": value.get("collection_initialized", False),
                "latest_new_count": value.get("collection_latest_new_count", 0),
                "consecutive_empty_swipes": value.get("collection_consecutive_empty_swipes", 0),
                "last_swipe": value.get("collection_last_swipe", ""),
                "traversal": value.get("collection_traversal", ""),
            }
            progress: object = visit.get("progress")
            if is_string_mapping(progress):
                evidence.update(
                    initialized=True,
                    latest_new_count=progress.get("latest_new_count", 0),
                    consecutive_empty_swipes=progress.get("empty_swipes", 0),
                    last_swipe=progress.get("last_swipe", ""),
                    traversal=progress.get("traversal", ""),
                )
            merchant = {
                "visit_id": visit_id,
                "phase": visit["phase"],
                "training_version": visit.get("training_version", ""),
                "entry_taps": visit.get("entry_taps", 0),
                "evidence": evidence,
            }
    pending: object = value.get("pending_visit")
    if is_string_mapping(pending):
        pending_kind: object = pending.get("kind")
        if not isinstance(pending_kind, str) or pending_kind not in names:
            raise ValueError("Unknown legacy candidate strategy")
        general["pending"] = {
            **{key: item for key, item in pending.items() if key != "kind"},
            "strategy": names[pending_kind],
        }
    probe: dict[str, object] = {
        key.removeprefix("probe_"): item for key, item in value.items() if key.startswith("probe_")
    }
    if "collection_visit_summaries" in value:
        probe["visit_summaries"] = value["collection_visit_summaries"]
    result: dict[str, object] = {
        key: item
        for key, item in value.items()
        if not key.startswith(("page_context_", "probe_", "collection_"))
        and key not in {"page_visit", "page_visit_id", "pending_visit"}
    }
    result.update(general=general, probe=probe)
    if merchant:
        result["merchant"] = merchant
    return result
