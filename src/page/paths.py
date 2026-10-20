"""Canonical page paths, shared by documents and visit training."""

import posixpath


def normalize_page_path(current_path: str) -> tuple[list[str], str | None]:
    raw_path: str = str(current_path or "").strip().replace("\\", "/")
    if raw_path.startswith("/"):
        return [], "Page path must be relative, not absolute."
    normalized: str = posixpath.normpath(raw_path).strip("/")
    if normalized in {"", "."}:
        return [], None
    if (
        normalized.startswith("../")
        or normalized == ".."
        or any(part == ".." for part in normalized.split("/"))
    ):
        return [], "Page path must not contain '..'."
    parts: list[str] = [part for part in normalized.split("/") if part and part != "."]
    if not parts:
        return [], None
    parts[0] = _app_slug(parts[0])
    return parts, None


def _app_slug(application_name: str) -> str:
    aliases: dict[str, str] = {
        "com.sankuai.meituan": "meituan",
        "美团": "meituan",
        "meituan": "meituan",
    }
    return aliases.get(application_name.strip(), application_name.strip())
