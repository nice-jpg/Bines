from __future__ import annotations


def run(xml: str, previous: dict | None = None) -> dict:
    return previous or {"items": []}
