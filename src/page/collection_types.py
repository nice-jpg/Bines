"""Page collection guide and script evaluation results."""

from typing import TypedDict


class CollectionGuide(TypedDict):
    content: str
    required_aliases: list[str]
    fingerprint: str
