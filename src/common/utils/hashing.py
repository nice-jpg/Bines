"""Stable content fingerprints."""

import hashlib


def fingerprint_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
