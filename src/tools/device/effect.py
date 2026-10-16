"""Typed policies for deterministic device-operation effect checks."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final

from src.serialization.validation import is_string_mapping

OPERATION_EFFECT_POLICY_METADATA_KEY: Final[str] = "bines_operation_effect_policy"


class OperationEffectKind(Enum):
    """Supported page-change expectations."""

    PAGE_CHANGE = "page_change"
    PARTIAL_CHANGE = "partial_change"


@dataclass(frozen=True)
class OperationEffectPolicy:
    """Declare the accepted similarity interval for one operation."""

    kind: OperationEffectKind
    minimum_similarity: float | None = None
    maximum_similarity: float | None = None
    minimum_inclusive: bool = True
    maximum_inclusive: bool = True

    def __post_init__(self) -> None:
        for value in (self.minimum_similarity, self.maximum_similarity):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError("Similarity boundaries must be between 0 and 1.")
        if (
            self.minimum_similarity is not None
            and self.maximum_similarity is not None
            and self.minimum_similarity > self.maximum_similarity
        ):
            raise ValueError("minimum_similarity must not exceed maximum_similarity.")

    def accepts(self, similarity: float) -> bool:
        """Return whether a similarity score satisfies this policy."""

        if self.minimum_similarity is not None:
            if self.minimum_inclusive and similarity < self.minimum_similarity:
                return False
            if not self.minimum_inclusive and similarity <= self.minimum_similarity:
                return False
        if self.maximum_similarity is not None:
            if self.maximum_inclusive and similarity > self.maximum_similarity:
                return False
            if not self.maximum_inclusive and similarity >= self.maximum_similarity:
                return False
        return True


NAVIGATION_PAGE_CHANGE_POLICY: Final[OperationEffectPolicy] = OperationEffectPolicy(
    kind=OperationEffectKind.PAGE_CHANGE,
    maximum_similarity=0.5,
    maximum_inclusive=False,
)
SCROLL_PAGE_CHANGE_POLICY: Final[OperationEffectPolicy] = OperationEffectPolicy(
    kind=OperationEffectKind.PARTIAL_CHANGE,
    minimum_similarity=0.05,
    maximum_similarity=0.95,
)


def operation_effect_policy_from_metadata(metadata: object) -> OperationEffectPolicy | None:
    """Read an operation-effect policy without inferring behavior from tool names."""

    if not is_string_mapping(metadata):
        return None
    policy: object = metadata.get(OPERATION_EFFECT_POLICY_METADATA_KEY)
    return policy if isinstance(policy, OperationEffectPolicy) else None


__all__: list[str] = [
    "OPERATION_EFFECT_POLICY_METADATA_KEY",
    "NAVIGATION_PAGE_CHANGE_POLICY",
    "SCROLL_PAGE_CHANGE_POLICY",
    "OperationEffectKind",
    "OperationEffectPolicy",
    "operation_effect_policy_from_metadata",
]
