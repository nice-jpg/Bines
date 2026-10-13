"""One immutable user input containing a complete batch of dialogue."""

from dataclasses import dataclass


@dataclass(frozen=True)
class MessageBlock:
    block_id: str
    text: str
