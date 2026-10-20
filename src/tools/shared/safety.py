"""Typed safety policies shared by tool builders and execution middleware."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, ClassVar, Final, Literal

from pydantic import BaseModel, ConfigDict, StringConstraints

from src.common.utils.validation import is_string_mapping

DANGEROUS_TOOL_POLICY_METADATA_KEY: Final[str] = "bines_dangerous_tool_policy"
type NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ToolRiskLevel(StrEnum):
    """Stable risk levels for tool execution policies."""

    DANGEROUS = "dangerous"


@dataclass(frozen=True)
class DangerousToolPolicy:
    """Metadata declaring that a tool requires an audited justification."""

    policy_id: str
    risk_level: ToolRiskLevel = ToolRiskLevel.DANGEROUS


class DangerousActionJustification(BaseModel):
    """Structured reason required before a dangerous tool can execute."""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    reason: NonEmptyText
    evidence: NonEmptyText
    expected_outcome: NonEmptyText
    risk_acknowledged: Literal[True]


def dangerous_policy_from_metadata(metadata: object) -> DangerousToolPolicy | None:
    """Return a typed policy from tool metadata without classifying by name."""

    if not is_string_mapping(metadata):
        return None
    policy: object = metadata.get(DANGEROUS_TOOL_POLICY_METADATA_KEY)
    return policy if isinstance(policy, DangerousToolPolicy) else None
